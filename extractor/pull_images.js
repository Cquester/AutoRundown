// AutoRundown extractor - IMAGE pull (proof of concept, read-only).
// Fetches WIS graphic blobs (ID2Blob_<cluster>, GraphikID <> 0) for a list of
// (DokID, GraphikID, Cluster) rows and writes each blob raw to
//   <AR_OUT>/raw/<graphik_nr>__<graphik_id>.bin
// plus pull_images.tsv (graphik_id, dok_id, cluster, bytes, ms, status).
//
// SELECT only. Modes (AR_MODE):
//   pull     (default) read AR_SAMPLE tsv (header: dok_id graphik_id graphik_nr
//            klasse cluster ...) and dump the blobs. Resumable: existing files skipped.
//   measure  per cluster, COUNT and total blob bytes of every graphic row
//            (GraphikID <> 0), in DokID-range chunks; and ID2Cluster counts
//            by GraphikKlasse. Writes measure.tsv / klasse.tsv.
//   probe    try a few size functions / key shapes and print what works.
//
// Config via environment: AR_HOST AR_PORT AR_DB AR_OUT AR_SAMPLE AR_MODE
// Run like pull.js: from a space-free staging dir next to tbjdbc.jar.
var System = Java.type("java.lang.System");
var DriverManager = Java.type("java.sql.DriverManager");
var JStr = Java.type("java.lang.String");
var File = Java.type("java.io.File");
var FileOutputStream = Java.type("java.io.FileOutputStream");
var FileWriter = Java.type("java.io.FileWriter");
var Files = Java.type("java.nio.file.Files");
var Paths = Java.type("java.nio.file.Paths");
var Thread = Java.type("java.lang.Thread");
Java.type("java.lang.Class").forName("transbase.jdbc.Driver");

function env(k, d){ var v = System.getenv(k); return (v==null||v=="") ? d : v; }
var HOST = env("AR_HOST", "192.168.56.101");
var PORT = env("AR_PORT", "2054");
var DB   = env("AR_DB",   "wisnet");
var OUT  = env("AR_OUT",  ".").replace(/\\/g, "/");
var MODE = env("AR_MODE", "pull");
var SLEEP = parseInt(env("AR_SLEEP", "50"), 10);   // ms between rows
var LAYOUT = env("AR_LAYOUT", "full");
var LIMIT = parseInt(env("AR_LIMIT", "0"), 10);   // stop after N new graphics (0 = all)
new File(OUT + "/raw").mkdirs();

function log(msg){
  var ts=new (Java.type("java.text.SimpleDateFormat"))("HH:mm:ss").format(new (Java.type("java.util.Date"))());
  print("["+ts+"] "+msg);
  var w=new FileWriter(OUT+"/pull_images.log",true); w.write("["+ts+"] "+msg+"\n"); w.close();
}
function append(path, line){ var w=new FileWriter(path,true); w.write(line+"\n"); w.close(); }

var url = "jdbc:transbase://"+HOST+":"+PORT+"/"+DB;
var conn = null;
function connect(){
  try { if (conn) conn.close(); } catch (e) {}
  conn = DriverManager.getConnection(url, "tbadmin", "");
  conn.setAutoCommit(true); conn.setReadOnly(true);
}
// run fn(conn); on failure reconnect once and retry once, then rethrow
function withRetry(what, fn){
  for (var attempt = 0; attempt < 2; attempt++) {
    try { return fn(conn); }
    catch (e) {
      log("WARN " + what + " attempt " + attempt + ": " + e);
      if (attempt == 1) throw e;
      Thread.sleep(2000); connect();
    }
  }
}
function scalar(sql){
  return withRetry(sql, function(c){
    var st=c.createStatement(); var rs=st.executeQuery(sql); rs.next();
    var v=rs.getString(1); rs.close(); st.close(); return v; });
}

connect();
log("IMAGES " + MODE + " start url=" + url + " out=" + OUT);

if (MODE == "probe") {
  var tries = [
    "SELECT COUNT(*) FROM ID2Blob_8 WHERE DokID >= 0 AND DokID < 1100000",
    "SELECT SIZE OF DokGraphikBlob FROM ID2Blob_8 WHERE DokID >= 0 AND DokID < 1100000 FIRST(1)",
    "SELECT SIZE(DokGraphikBlob) FROM ID2Blob_8 WHERE DokID >= 0 AND DokID < 1100000",
    "SELECT OCTET_LENGTH(DokGraphikBlob) FROM ID2Blob_8 WHERE DokID >= 0 AND DokID < 1100000",
    "SELECT SUM(SIZE OF DokGraphikBlob) FROM ID2Blob_8 WHERE DokID >= 1000000 AND DokID < 1010000",
    "SELECT MIN(DokID), MAX(DokID), MIN(GraphikID), MAX(GraphikID) FROM ID2Blob_8",
    "SELECT COUNT(*) FROM ID2Blob_8 WHERE GraphikID = 0",
    "SELECT * FROM GraphikStatistik",
    "SELECT * FROM BlobAnzahl"
  ];
  for (var i = 0; i < tries.length; i++) {
    try {
      var st=conn.createStatement(); st.setMaxRows(5); var rs=st.executeQuery(tries[i]);
      var md=rs.getMetaData(), nc=md.getColumnCount(), rows=[];
      while (rs.next() && rows.length < 5) { var r=[]; for (var c=1;c<=nc;c++) r.push(rs.getString(c)); rows.push(r.join("|")); }
      rs.close(); st.close();
      log("OK   " + tries[i] + "  ->  " + rows.join(" ; "));
    } catch (e) { log("FAIL " + tries[i] + "  ->  " + e); try { connect(); } catch (e2) {} }
  }
  // key shape: is a graphic stored under the citing DokID, or elsewhere?
  var sample = env("AR_SAMPLE", "");
  if (sample) {
    var lines = Files.readAllLines(Paths.get(sample)); var n = 0;
    for (var i = 1; i < lines.size() && n < 6; i++, n++) {
      var f = (""+lines.get(i)).split("\t"), dok=f[0], gid=f[1], cl=f[4];
      log("key " + dok + "/" + gid + " cl" + cl + ": byDok+Gid=" +
          scalar("SELECT COUNT(*) FROM ID2Blob_"+cl+" WHERE DokID="+dok+" AND GraphikID="+gid) +
          " byGid(all doks, this cluster)=" + scalar("SELECT COUNT(*) FROM ID2Blob_"+cl+" WHERE GraphikID="+gid) +
          " ID2Cluster rows for gid=" + scalar("SELECT COUNT(*) FROM ID2Cluster WHERE GraphikID="+gid));
    }
  }
}

if (MODE == "pull") {
  var lines = Files.readAllLines(Paths.get(env("AR_SAMPLE", OUT + "/sample.tsv")));
  var idx = OUT + "/pull_images.tsv";
  if (!new File(idx).exists()) append(idx, "graphik_id\tgraphik_nr\tdok_id\tcluster\tblob_dok_id\tbytes\tms\tstatus");
  var ok=0, miss=0, totalB=0, totalMs=0;
  for (var i = 1; i < lines.size(); i++) {
    var f = (""+lines.get(i)).split("\t");
    var dok=parseInt(f[0],10), gid=parseInt(f[1],10), nr=f[2], cl=parseInt(f[4],10);
    // full pass: raw/<cluster>/<graphik_id>.bin (AR_LAYOUT=sample keeps the
    // proof-of-concept naming <graphik_nr>__<graphik_id>.bin)
    var dst = LAYOUT == "sample" ? OUT + "/raw/" + nr + "__" + gid + ".bin"
                                 : OUT + "/raw/" + cl + "/" + gid + ".bin";
    if (LAYOUT != "sample") new File(OUT + "/raw/" + cl).mkdirs();
    if (new File(dst).exists()) continue;
    var t0 = System.currentTimeMillis(), blobDok = -1;
    var b = withRetry("blob " + gid, function(c){
      // A graphic is stored ONCE, not under each citing document: the row is
      // keyed (DokID = BLOB_DOK, GraphikID). Probe showed DokID+GraphikID of
      // the citing doc never matches; GraphikID alone matches exactly one row.
      var ps=c.prepareStatement("SELECT DokID, DokGraphikBlob FROM ID2Blob_"+cl+" WHERE DokID = 0 AND GraphikID=?");
      ps.setInt(1,gid); var rs=ps.executeQuery();
      var bytes = null; if (rs.next()) { blobDok = rs.getInt(1); bytes = rs.getBytes(2); }
      rs.close(); ps.close(); return bytes; });
    var ms = System.currentTimeMillis() - t0;
    if (b == null || b.length == 0) { miss++; append(idx, gid+"\t"+nr+"\t"+dok+"\t"+cl+"\t"+blobDok+"\t0\t"+ms+"\tMISSING"); continue; }
    var o = new FileOutputStream(dst); o.write(b); o.close();
    ok++; totalB += b.length; totalMs += ms;
    append(idx, gid+"\t"+nr+"\t"+dok+"\t"+cl+"\t"+blobDok+"\t"+b.length+"\t"+ms+"\tok");
    if (LIMIT && ok >= LIMIT) { log("batch limit " + LIMIT + " reached at row " + i); break; }
    if (ok % 2000 == 0) log("progress: row " + i + "/" + (lines.size()-1) + " ok=" + ok + " missing=" + miss + " MB=" + Math.round(totalB/1048576));
    Thread.sleep(SLEEP);
  }
  log("pull done: ok="+ok+" missing="+miss+" bytes="+totalB+" ms="+totalMs);
}

if (MODE == "measure") {
  // Graphics live at DokID = 0 (one row per GraphikID, shared by every citing
  // document); bodies are GraphikID = 0. Count both shapes, then sum graphic
  // sizes in GraphikID-range chunks (the DB drops connections on big scans).
  var mpath = OUT + "/measure.tsv";
  if (!new File(mpath).exists()) append(mpath, "cluster\tgid_lo\tgid_hi\tgraphic_rows\tgraphic_bytes\tmin_bytes\tmax_bytes\tms");
  var SIZE = env("AR_SIZEFN", "SIZE OF DokGraphikBlob");
  var CH = parseInt(env("AR_CHUNK", "50000"), 10);
  var clusters = env("AR_CLUSTERS", "2,3,4,5,6,7,8").split(",");
  for (var k = 0; k < clusters.length; k++) {
    var cl = clusters[k];
    var nGfx = scalar("SELECT COUNT(*) FROM ID2Blob_"+cl+" WHERE GraphikID <> 0");
    var nGfx0 = scalar("SELECT COUNT(*) FROM ID2Blob_"+cl+" WHERE DokID = 0 AND GraphikID <> 0");
    var hi = parseInt(scalar("SELECT MAX(GraphikID) FROM ID2Blob_"+cl), 10);
    log("cluster " + cl + " graphic rows=" + nGfx + " of which DokID=0: " + nGfx0 + " max GraphikID=" + hi);
    for (var a = 1; a <= hi; a += CH) {
      var b2 = a + CH, t0 = System.currentTimeMillis();
      var r = withRetry("measure "+cl+"@"+a, function(c){
        var st=c.createStatement();
        var rs=st.executeQuery("SELECT COUNT(*), SUM(" + SIZE + "), MIN(" + SIZE + "), MAX(" + SIZE + ") FROM ID2Blob_"+cl+
          " WHERE GraphikID >= "+a+" AND GraphikID < "+b2);
        rs.next(); var res=[rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4)];
        rs.close(); st.close(); return res; });
      append(mpath, cl+"\t"+a+"\t"+b2+"\t"+r.join("\t")+"\t"+(System.currentTimeMillis()-t0));
      Thread.sleep(200);
    }
  }
  // graphic link counts by class (all languages)
  var kpath = OUT + "/klasse.tsv";
  var r = withRetry("klasse", function(c){
    var st=c.createStatement();
    var rs=st.executeQuery("SELECT GraphikKlasse, Cluster, COUNT(*) FROM ID2Cluster WHERE GraphikID <> 0 GROUP BY GraphikKlasse, Cluster");
    var out=[]; while (rs.next()) out.push(rs.getString(1)+"\t"+rs.getString(2)+"\t"+rs.getString(3));
    rs.close(); st.close(); return out; });
  var w=new FileWriter(kpath,false); w.write("klasse\tcluster\tid2cluster_rows\n"+r.join("\n")+"\n"); w.close();
  log("measure done");
}

if (MODE == "sizes") {
  // every graphic's byte size, no blob bytes transferred: sizes.tsv
  var spath = OUT + "/sizes.tsv";
  var w = new FileWriter(spath, false); w.write("cluster\tgraphik_id\tbytes\n");
  var clusters = env("AR_CLUSTERS", "2,3,4,5,6,7,8").split(",");
  for (var k = 0; k < clusters.length; k++) {
    var cl = clusters[k];
    var hi = parseInt(scalar("SELECT MAX(GraphikID) FROM ID2Blob_"+cl), 10);
    for (var a = 1; a <= hi; a += 100000) {
      var rows = withRetry("sizes "+cl+"@"+a, function(c){
        var st=c.createStatement(); st.setFetchSize(5000);
        var rs=st.executeQuery("SELECT GraphikID, SIZE OF DokGraphikBlob FROM ID2Blob_"+cl+
          " WHERE DokID = 0 AND GraphikID >= "+a+" AND GraphikID < "+(a+100000));
        var out=[]; while (rs.next()) out.push(cl+"\t"+rs.getString(1)+"\t"+rs.getString(2));
        rs.close(); st.close(); return out; });
      if (rows.length) w.write(rows.join("\n")+"\n");
      Thread.sleep(200);
    }
    log("sizes cluster " + cl + " done");
  }
  w.close();
}
conn.close();
