// AutoRundown extractor - DIAGRAM LINK pull (resumable, batched by DokID).
// For the documents in AR_LIST (one DokID per line: the corpus's diagram
// documents), dumps the rows of
//
//   DXF             DokID -> drawing extent (MaxXpos, MaxYpos)
//   DXFLegendePos   component code positions on the drawing (Xpos, Ypos)
//   DXFLegende      component code -> name ("N3/10" -> "ME control unit")
//   AW_DXFReferenz  clickable boxes on the drawing -> target docnr
//   AW_SVGReferenz  component code -> target docnr, for SVG diagrams
//
// These tables are 1-11M rows across every language; filtering by our own
// DokIDs keeps only the English rows. Positions are in drawing units with Y
// up from the bottom edge (checked on 25131415: scale to the converted SVG's
// width and flip Y, and every code lands on its printed label).
//
// Resumable: each batch is appended to every table's TSV, then its number is
// written to links_progress.txt. A connection drop mid-batch exits non-zero
// (vm_pipeline power-cycles and re-runs); that batch is redone, so a table
// may hold one batch twice -- parse_links.py drops duplicate rows.
//
//   AR_HOST AR_PORT AR_DB AR_OUT as the other pulls; AR_LIST the DokID list;
//   AR_BATCH DokIDs per query (default 400).
var System = Java.type("java.lang.System");
var DriverManager = Java.type("java.sql.DriverManager");
var JStr = Java.type("java.lang.String");
var File = Java.type("java.io.File");
var FileOutputStream = Java.type("java.io.FileOutputStream");
var FileWriter = Java.type("java.io.FileWriter");
var Files = Java.type("java.nio.file.Files");
var Paths = Java.type("java.nio.file.Paths");
Java.type("java.lang.Class").forName("transbase.jdbc.Driver");

function env(k, d){ var v = System.getenv(k); return (v==null||v=="") ? d : v; }
var OUT = env("AR_OUT", ".").replace(/\\/g, "/");
var LIST = env("AR_LIST", OUT + "/links_list.txt");
var BATCH = parseInt(env("AR_BATCH", "400"), 10);
var TABLES = ["DXF", "DXFLegendePos", "DXFLegende", "AW_DXFReferenz", "AW_SVGReferenz"];
new File(OUT).mkdirs();

function log(msg){
  var ts = new (Java.type("java.text.SimpleDateFormat"))("HH:mm:ss").format(new (Java.type("java.util.Date"))());
  print("[" + ts + "] " + msg);
  var w = new FileWriter(OUT + "/pull_links.log", true); w.write("[" + ts + "] " + msg + "\n"); w.close();
}
function s(v){ return v==null ? "" : (""+v).replace(/[\t\r\n]/g," ").trim(); }

var ids = [];
var lines = Files.readAllLines(Paths.get(LIST));
for (var i = 0; i < lines.size(); i++) { var t = s(lines.get(i)); if (/^\d+$/.test(t)) ids.push(t); }
var batches = Math.ceil(ids.length / BATCH);
var prog = new File(OUT + "/links_progress.txt");
var done = prog.exists() ? parseInt(s(new java.lang.String(Files.readAllBytes(prog.toPath()), "UTF-8")), 10) : -1;
if (isNaN(done)) done = -1;
log("LINK pull: " + ids.length + " documents, " + batches + " batches of " + BATCH +
    (done >= 0 ? ", resuming after batch " + done : ""));

var url = "jdbc:transbase://" + env("AR_HOST", "192.168.56.101") + ":" + env("AR_PORT", "2054") + "/" + env("AR_DB", "wisnet");
var conn = DriverManager.getConnection(url, "tbadmin", "");
conn.setAutoCommit(true);

var totals = {};
for (var b = done + 1; b < batches; b++) {
  var inList = ids.slice(b * BATCH, (b + 1) * BATCH).join(",");
  var chunks = {};
  try {
    for (var t = 0; t < TABLES.length; t++) {
      var name = TABLES[t];
      var st = conn.createStatement(); st.setFetchSize(2000);
      var rs = st.executeQuery("SELECT * FROM " + name + " WHERE DokID IN (" + inList + ")");
      var md = rs.getMetaData(), nc = md.getColumnCount(), buf = [];
      if (b == 0 || !new File(OUT + "/" + name + ".tsv").exists()) {
        var cols = []; for (var c = 1; c <= nc; c++) cols.push(md.getColumnName(c));
        buf.push(cols.join("\t"));
      }
      while (rs.next()) {
        var row = []; for (var c = 1; c <= nc; c++) row.push(s(rs.getString(c)));
        buf.push(row.join("\t"));
      }
      rs.close(); st.close();
      chunks[name] = buf;
    }
  } catch (e) {
    log("ABORT at batch " + b + ": " + e);
    conn.close();
    System.exit(3);
  }
  // all five read: now append, then record the batch as done
  for (var t = 0; t < TABLES.length; t++) {
    var name = TABLES[t], buf = chunks[name];
    if (buf.length) {
      var o = new FileOutputStream(OUT + "/" + name + ".tsv", true);
      o.write(JStr.valueOf(buf.join("\n") + "\n").getBytes("UTF-8")); o.close();
    }
    totals[name] = (totals[name] || 0) + buf.length;
  }
  var pw = new FileWriter(prog, false); pw.write("" + b); pw.close();
  if (b % 10 == 0 || b == batches - 1) {
    var parts = []; for (var k in totals) parts.push(k + "+" + totals[k]);
    log("batch " + (b + 1) + "/" + batches + "  " + parts.join(" "));
  }
}
conn.close();
log("LINK pull done");
