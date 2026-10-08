// AutoRundown extractor - APPLICABILITY candidates pull (investigation, read-only).
// Dumps candidate document->model tables whole, every column, to TSV, so the
// real applicability join can be validated offline against the text.
//
//   W_Kurzgueltigkeit   KurzgueltigkeitID -> short-validity text (per document)
//   WS1..3, WC1..9      BMZeilenNr <-> ReihenID (navigation node -> model line)
//   WCU1..9             ReihenID -> attribute bits
//   Dokumente1..10      DokNrID -> ReihenID (+ InfoArt / FGR / FUG)
//   W_Dokumente         DokNrID -> DokNr, InfoArt, FGR, FUG
//   AWS2Dokumente, SA2Dokumente   other DokNrID -> ReihenID links
//
// SELECT only. Resumable: a table whose .tsv already exists is skipped, and
// big tables are read in key-range chunks (the WIS DB drops connections under
// load), each chunk on a fresh statement. A failed table leaves no .tsv.
//
// Config via environment: AR_HOST AR_PORT AR_DB AR_OUT  (AR_TABLES optional,
// comma list to restrict).
var System = Java.type("java.lang.System");
var DriverManager = Java.type("java.sql.DriverManager");
var JStr = Java.type("java.lang.String");
var File = Java.type("java.io.File");
var FileOutputStream = Java.type("java.io.FileOutputStream");
var BufferedOutputStream = Java.type("java.io.BufferedOutputStream");
var FileWriter = Java.type("java.io.FileWriter");
Java.type("java.lang.Class").forName("transbase.jdbc.Driver");

function env(k, d){ var v = System.getenv(k); return (v==null||v=="") ? d : v; }
var HOST = env("AR_HOST", "192.168.56.101");
var PORT = env("AR_PORT", "2054");
var DB   = env("AR_DB",   "wisnet");
var OUT  = env("AR_OUT",  ".").replace(/\\/g, "/");
new File(OUT).mkdirs();

function log(msg){
  var ts=new (Java.type("java.text.SimpleDateFormat"))("HH:mm:ss").format(new (Java.type("java.util.Date"))());
  print("["+ts+"] "+msg);
  var w=new FileWriter(OUT+"/pull_applic2.log",true); w.write("["+ts+"] "+msg+"\n"); w.close();
}
function s(v){ return v==null ? "" : (""+v).replace(/[\t\r\n]/g," ").trim(); }

// [table, chunk key or null, chunk size]
var TABLES = [
  ["W_Kurzgueltigkeit", "KurzgueltigkeitID", 50000],
  ["W_Dokumente", "DokNrID", 100000],
  ["WS1", null], ["WS2", null], ["WS3", null],
  ["WC1", null], ["WC2", null], ["WC3", null], ["WC4", null], ["WC5", null],
  ["WC6", null], ["WC7", null], ["WC8", null], ["WC9", null],
  ["WCU1", null], ["WCU2", null], ["WCU3", null], ["WCU4", null], ["WCU5", null],
  ["WCU6", null], ["WCU7", null], ["WCU8", null], ["WCU9", null],
  ["Dokumente1", "DokNrID", 200000], ["Dokumente2", null], ["Dokumente3", null],
  ["Dokumente4", null], ["Dokumente6", null],
  ["Dokumente8", null], ["Dokumente9", null], ["Dokumente10", null],
  ["AWS2Dokumente", null], ["SA2Dokumente", null],
  ["Baureihen", null], ["BaumusterZeilen", null], ["FzgArt", null]
];
var only = env("AR_TABLES", "");
if (only) { var want = only.split(","); TABLES = TABLES.filter(function(t){ return want.indexOf(t[0]) >= 0; }); }

var url = "jdbc:transbase://"+HOST+":"+PORT+"/"+DB;
log("APPLIC2 pull start url="+url+" out="+OUT);
var conn = DriverManager.getConnection(url, "tbadmin", "");
conn.setAutoCommit(true);
conn.setReadOnly(true);

function scalar(sql){
  var st = conn.createStatement(); var rs = st.executeQuery(sql); rs.next();
  var v = rs.getString(1); rs.close(); st.close(); return v;
}

var failed = 0, summary = [];
for (var t = 0; t < TABLES.length; t++) {
  var name = TABLES[t][0], key = TABLES[t][1], chunk = TABLES[t][2];
  var dst = new File(OUT + "/" + name + ".tsv");
  if (dst.exists()) { log(name + " exists, skipped"); continue; }
  var part = OUT + "/" + name + ".tsv.part";
  try {
    var queries = [];
    if (key) {
      var lo = parseInt(scalar("SELECT MIN(" + key + ") FROM " + name), 10);
      var hi = parseInt(scalar("SELECT MAX(" + key + ") FROM " + name), 10);
      for (var a = lo; a <= hi; a += chunk)
        queries.push("SELECT * FROM " + name + " WHERE " + key + " >= " + a + " AND " + key + " < " + (a + chunk));
    } else queries.push("SELECT * FROM " + name);
    var out = new BufferedOutputStream(new FileOutputStream(part, false));
    var n = 0, header = false;
    for (var q = 0; q < queries.length; q++) {
      var st = conn.createStatement(); st.setFetchSize(2000);
      var rs = st.executeQuery(queries[q]);
      var md = rs.getMetaData(), nc = md.getColumnCount();
      if (!header) {
        var cols = []; for (var c = 1; c <= nc; c++) cols.push(md.getColumnName(c));
        out.write(JStr.valueOf(cols.join("\t") + "\n").getBytes("UTF-8")); header = true;
      }
      while (rs.next()) {
        var row = []; for (var c = 1; c <= nc; c++) row.push(s(rs.getString(c)));
        out.write(JStr.valueOf(row.join("\t") + "\n").getBytes("UTF-8")); n++;
      }
      rs.close(); st.close(); out.flush();
      if (queries.length > 1) Java.type("java.lang.Thread").sleep(200);
    }
    out.close();
    new File(part).renameTo(dst);
    log(name + " rows = " + n);
    summary.push(name + "=" + n);
  } catch (e) {
    failed++;
    log("FAILED " + name + ": " + e);
    try { out.close(); } catch (e2) {}
    new File(part)["delete"]();
  }
}
conn.close();
log("APPLIC2 pull done: " + summary.join(" ") + (failed ? "  FAILED TABLES: " + failed : ""));
if (failed) System.exit(2);
