// Pull benchmark (read-only, writes nothing but its own log): the same queries
// as pull.js (documents) and pull_images.js (graphics), with knobs for pacing
// and for what happens when the database drops the connection. Blob bytes are
// counted and thrown away.
//
// Env: AR_HOST AR_PORT AR_DB
//   B_KIND            images | docs
//   B_LIST            images: list.tsv (dok_id graphik_id graphik_nr klasse cluster)
//   B_FROM            images: first row (1 = first data row); docs: start after this DokID
//   B_COUNT           items to pull
//   B_SLEEP_MS        sleep after every B_SLEEP_EVERY items (default every item)
//   B_SLEEP_EVERY
//   B_FRESH_EVERY     open a new connection every N items (0 = never)
//   B_CURSOR_ROWS     docs: re-open the document list every N rows (0 = never)
//   B_RETRIES         on a drop: reconnect attempts before giving up (0 = give up at once)
//   B_RETRY_WAIT_MS   wait before attempt k is k * this
//
// Prints "EV <ms> <what> <detail>" per event and one final
// "END <status> <items> <bytes> <ms> <next>" (status ok | dead; next = where to resume).
var System = Java.type("java.lang.System");
var DriverManager = Java.type("java.sql.DriverManager");
var Files = Java.type("java.nio.file.Files");
var Paths = Java.type("java.nio.file.Paths");
var Thread = Java.type("java.lang.Thread");
Java.type("java.lang.Class").forName("transbase.jdbc.Driver");

function env(k, d){ var v = System.getenv(k); return (v==null||v=="") ? d : v; }
function num(k, d){ return parseInt(env(k, ""+d), 10); }
var url = "jdbc:transbase://"+env("AR_HOST","192.168.56.101")+":"+env("AR_PORT","2054")+"/"+env("AR_DB","wisnet");
var KIND = env("B_KIND", "images"), FROM = num("B_FROM", 1), COUNT = num("B_COUNT", 1000);
var SLEEP = num("B_SLEEP_MS", 0), SLEEP_EVERY = Math.max(1, num("B_SLEEP_EVERY", 1));
var FRESH = num("B_FRESH_EVERY", 0), CURSOR_ROWS = num("B_CURSOR_ROWS", 0);
var RETRIES = num("B_RETRIES", 0), RETRY_WAIT = num("B_RETRY_WAIT_MS", 5000);
var T0 = System.currentTimeMillis();
function ev(what, detail){ print("EV " + (System.currentTimeMillis()-T0) + " " + what + " " + (detail||"")); }

var conn = null;
function connect(){
  try { if (conn) conn.close(); } catch (e) {}
  conn = null;
  conn = DriverManager.getConnection(url, "tbadmin", "");
  conn.setAutoCommit(true);
}
// a drop: reconnect up to RETRIES times; true if the database took us back
function recover(where, e){
  var t = System.currentTimeMillis();
  ev("drop", where + " " + (""+e).replace(/\s+/g, " ").substring(0, 160));
  for (var k = 1; k <= RETRIES; k++) {
    Thread.sleep(RETRY_WAIT * k);
    try {
      connect();
      var st = conn.createStatement(); var rs = st.executeQuery("SELECT COUNT(*) FROM W_SprachDokumente WHERE DokID = 0");
      rs.next(); rs.close(); st.close();
      ev("reconnected", "attempt " + k + " after " + (System.currentTimeMillis()-t) + " ms");
      return true;
    } catch (e2) { ev("reconnect_failed", "attempt " + k + " " + (""+e2).replace(/\s+/g, " ").substring(0, 120)); }
  }
  return false;
}

var items = 0, bytes = 0, next = FROM, status = "ok";
connect();
ev("start", KIND + " from " + FROM + " count " + COUNT);

if (KIND == "images") {
  var lines = Files.readAllLines(Paths.get(env("B_LIST", "list.tsv")));
  var last = Math.min(lines.size() - 1, FROM + COUNT - 1);
  for (var i = FROM; i <= last; i++) {
    var f = (""+lines.get(i)).split("\t"), gid = parseInt(f[1],10), cl = parseInt(f[4],10);
    var done = false;
    while (!done) {
      try {
        var ps = conn.prepareStatement("SELECT DokID, DokGraphikBlob FROM ID2Blob_"+cl+" WHERE DokID = 0 AND GraphikID=?");
        ps.setInt(1, gid); var rs = ps.executeQuery();
        if (rs.next()) { var b = rs.getBytes(2); if (b != null) bytes += b.length; }
        rs.close(); ps.close(); done = true;
      } catch (e) {
        if (!recover("row " + i, e)) { status = "dead"; break; }
      }
    }
    if (status == "dead") { next = i; break; }
    items++; next = i + 1;
    if (FRESH && items % FRESH == 0) { try { connect(); ev("fresh", "at " + items); } catch (e) { if (!recover("fresh", e)) { status = "dead"; break; } } }
    if (SLEEP && items % SLEEP_EVERY == 0) Thread.sleep(SLEEP);
    if (items % 5000 == 0) ev("progress", items + " " + Math.round(bytes/1048576) + "MB");
  }
}

if (KIND == "docs") {
  var LANG = 2, lastDok = FROM;
  var psG = null, bodyPs = {};
  function prep(){
    psG = conn.prepareStatement("SELECT GraphikID, GraphikNr, GraphikKlasse, Cluster FROM ID2Cluster WHERE DokID=?");
    bodyPs = {};
  }
  function bodyStmt(cl){
    if (!bodyPs[cl]) bodyPs[cl] = conn.prepareStatement("SELECT DokGraphikBlob FROM ID2Blob_"+cl+" WHERE DokID=? AND GraphikID=0");
    return bodyPs[cl];
  }
  prep();
  outer:
  while (items < COUNT) {
    var main = null, rs = null, rows = 0;
    try {
      main = conn.createStatement(); main.setFetchSize(2000);
      rs = main.executeQuery("SELECT DokID, DokNrID, DokTypCode, FzgArtCodes, KurzgueltigkeitID, DokTitel "+
        "FROM W_SprachDokumente WHERE SprachCode="+LANG+" AND DokTitel IS NOT NULL AND DokID > "+lastDok+" ORDER BY DokID");
      var more = false;
      while (items < COUNT && rs.next()) {
        more = true;
        var dokid = rs.getInt(1); rs.getInt(2); rs.getInt(3); rs.getString(4); rs.getInt(5); rs.getString(6);
        var cl = -1;
        psG.setInt(1, dokid); var rg = psG.executeQuery();
        while (rg.next()) { if (rg.getInt(1) === 0) cl = rg.getInt(4); rg.getString(2); rg.getInt(3); }
        rg.close();
        if (cl >= 0) {
          var bs = bodyStmt(cl); bs.setInt(1, dokid); var rb = bs.executeQuery();
          if (rb.next()) { var b = rb.getBytes(1); if (b != null) bytes += b.length; }
          rb.close();
        }
        items++; rows++; lastDok = dokid; next = dokid;
        if (SLEEP && items % SLEEP_EVERY == 0) Thread.sleep(SLEEP);
        if (items % 5000 == 0) ev("progress", items + " " + Math.round(bytes/1048576) + "MB at " + dokid);
        if (FRESH && items % FRESH == 0) { rs.close(); main.close(); connect(); prep(); ev("fresh", "at " + items); continue outer; }
        if (CURSOR_ROWS && rows >= CURSOR_ROWS) break;
      }
      rs.close(); main.close();
      if (!more) break;
    } catch (e) {
      if (!recover("doc after " + lastDok, e)) { status = "dead"; break; }
      try { prep(); } catch (e3) { status = "dead"; break; }
    }
  }
}

try { conn.close(); } catch (e) {}
print("END " + status + " " + items + " " + bytes + " " + (System.currentTimeMillis()-T0) + " " + next);
