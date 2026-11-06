// AutoRundown extractor - VEHICLE pull (config-driven, small, language-free).
// Dumps the vehicle/VIN reference tables whole, every column, to TSV:
//
//   ASRA_FahrzeugII    one row per vehicle (WMI + Baumuster): sales name,
//                      short description, first/last MODEL YEAR, engine range
//   ASRA_Fahrzeug      same vehicles: WMI, MotorBM, production month span
//   AW_Fahrzeuge       Baumuster -> engine, type name, sales designation
//   USTypBM2TypBM      US VINs: WMI + one character -> model series
//   USMotKar2TypBM4    US VINs: one character -> 4th Baumuster digit
//   Modelljahr2SACode  model year <-> SA (option) code
//   ASRA_Typkennzahl   type-number names (body styles), per language
//   FzgArt             vehicle-type names for the fzg_art bits, per language
//
// These answer "which car is this": names for the picker, real model years,
// and the US half of VIN decoding (a US VIN does not carry the Baumuster the
// way a European FIN does). Columns are read from the driver's metadata, so
// nothing here depends on guessing the schema.
//
// A table that fails is reported and the run exits non-zero -- never a partial
// file that looks complete.
//
// Config via environment (set by extract.ps1, or by hand):
//   AR_HOST  guest DB host   AR_PORT port   AR_DB database   AR_OUT output dir
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
  var w=new FileWriter(OUT+"/pull_vehicles.log",true); w.write("["+ts+"] "+msg+"\n"); w.close();
}
function s(v){ return v==null ? "" : (""+v).replace(/[\t\r\n]/g," ").trim(); }

// The tables and their filters are in vehicle_tables.json, next to this
// script (what each is for is its "why"): ledger.py reads the same list to
// tell which tables a re-run still has to pull (2026-11-05). Multi-language
// tables are filtered to the language being pulled (AR_LANG, 2 = English):
// the others are 10-30x larger and never read.
var LANG = parseInt(env("AR_LANG", "2"), 10);
var LANG2 = (LANG < 10 ? "0" : "") + LANG;     // ASRA writes it as "02"
// (vm_pipeline.py stages it beside the script; Nashorn runs from there)
var Files = Java.type("java.nio.file.Files");
var SPEC = JSON.parse("" + new JStr(Files.readAllBytes(new File("vehicle_tables.json").toPath()), "UTF-8"));
var TABLES = [], WHERE = {};
for (var i = 0; i < SPEC.tables.length; i++) {
  var t = SPEC.tables[i];
  TABLES.push(t.name);
  if (t.where) WHERE[t.name] = t.where.split("{LANG2}").join(LANG2).split("{LANG}").join("" + LANG);
}
// AR_TABLES=a,b pulls just those (e.g. to add a new table without redoing all)
if (env("AR_TABLES", "")) TABLES = env("AR_TABLES", "").split(",");

var url = "jdbc:transbase://"+HOST+":"+PORT+"/"+DB;
log("VEHICLE pull start url="+url+" out="+OUT);
var conn = DriverManager.getConnection(url, "tbadmin", "");
conn.setAutoCommit(true);

var failed = 0, summary = [];
for (var t = 0; t < TABLES.length; t++) {
  var name = TABLES[t];
  // write to .part and rename only on success, so a failed table never
  // leaves a file that looks complete
  var part = OUT + "/" + name + ".tsv.part";
  try {
    var st = conn.createStatement(); st.setFetchSize(2000);
    var sql = "SELECT * FROM " + name + (WHERE[name] ? " WHERE " + WHERE[name] : "");
    var rs = st.executeQuery(sql);
    var md = rs.getMetaData(), nc = md.getColumnCount();
    var out = new BufferedOutputStream(new FileOutputStream(part, false));
    var cols = [], blob = [];
    for (var c = 1; c <= nc; c++) {
      cols.push(md.getColumnName(c));
      blob.push(md.getColumnTypeName(c).toLowerCase().indexOf("blob") >= 0);
    }
    out.write(JStr.valueOf(cols.join("\t") + "\n").getBytes("UTF-8"));
    var n = 0;
    while (rs.next()) {
      var row = [];
      for (var c = 1; c <= nc; c++) {
        if (!blob[c - 1]) { row.push(s(rs.getString(c))); continue; }
        // a text blob: UTF-8, its line breaks kept as a literal \n
        var b = rs.getBytes(c);
        row.push(b == null ? "" : ("" + new JStr(b, "UTF-8")).replace(/\r\n?/g, "\n")
                 .replace(/\t/g, " ").replace(/\\/g, "/").trim().replace(/\n/g, "\\n"));
      }
      out.write(JStr.valueOf(row.join("\t") + "\n").getBytes("UTF-8"));
      n++;
    }
    rs.close(); st.close(); out.flush(); out.close();
    var dst = new File(OUT + "/" + name + ".tsv");
    if (dst.exists()) dst["delete"]();
    new File(part).renameTo(dst);
    // the query beside the table: ledger.py pulls a table again only when
    // this file is missing or the list now asks for a different query
    var q = new FileOutputStream(OUT + "/" + name + ".sql", false);
    q.write(JStr.valueOf(sql).getBytes("UTF-8")); q.close();
    log(name + " rows = " + n + "  (" + nc + " columns)");
    summary.push(name + "=" + n);
  } catch (e) {
    failed++;
    log("FAILED " + name + ": " + e);
    new File(part)["delete"]();
  }
}
conn.close();
log("VEHICLE pull done: " + summary.join(" ") + (failed ? "  FAILED TABLES: " + failed : ""));
if (failed) System.exit(2);
