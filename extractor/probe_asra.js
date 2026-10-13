// AutoRundown extractor - read-only probe of WIS's labour-time (ASRA) notes.
// Writes the ASRA preface for one language (ASRA_Vorwort: a blob, the
// document that explains the time unit and how to read the tables) and the
// region formula tables (ASRA_Formel, ASRA_Formelverwendung, ASRA_Region),
// which adjust the stored values per market. SELECT only.
//
//   AR_HOST AR_PORT AR_DB AR_OUT as the other pulls; AR_LANG2 ("02" English).
var System = Java.type("java.lang.System");
var DriverManager = Java.type("java.sql.DriverManager");
var JStr = Java.type("java.lang.String");
var File = Java.type("java.io.File");
var FileOutputStream = Java.type("java.io.FileOutputStream");
Java.type("java.lang.Class").forName("transbase.jdbc.Driver");

function env(k, d){ var v = System.getenv(k); return (v==null||v=="") ? d : v; }
var OUT = env("AR_OUT", ".").replace(/\\/g, "/");
var LANG2 = env("AR_LANG2", "02");
new File(OUT).mkdirs();
var conn = DriverManager.getConnection("jdbc:transbase://" + env("AR_HOST", "192.168.56.101") + ":" +
    env("AR_PORT", "2054") + "/" + env("AR_DB", "wisnet"), "tbadmin", "");
function s(v){ return v==null ? "" : (""+v).replace(/[\t\r\n]/g," ").trim(); }

// the preface blobs, one file per row, extension from the Format column
var st = conn.createStatement();
var rs = st.executeQuery("SELECT VorwortTyp, VorwortDatei, SequenzNummerFormat, Format FROM ASRA_Vorwort WHERE SprachAbk = '" + LANG2 + "'");
var n = 0;
while (rs.next()) {
  var bytes = rs.getBytes(2);
  var name = OUT + "/vorwort_" + LANG2 + "_" + s(rs.getString(1)) + "_" + s(rs.getString(3)) + "." + (s(rs.getString(4)) || "bin").toLowerCase();
  if (bytes != null) { var o = new FileOutputStream(name); o.write(bytes); o.close(); }
  print("preface " + name + " " + (bytes == null ? 0 : bytes.length) + " bytes");
  n++;
}
rs.close(); st.close();
print(n + " preface row(s)");

// the small tables, whole, as TSV
var TABLES = ["ASRA_Formel", "ASRA_Formelverwendung", "ASRA_Region", "ASRA_Sparte", "ASRA_Produktionsland"];
for (var t = 0; t < TABLES.length; t++) {
  var st2 = conn.createStatement();
  var r = st2.executeQuery("SELECT * FROM " + TABLES[t]);
  var m = r.getMetaData(), nc = m.getColumnCount(), cols = [];
  for (var c = 1; c <= nc; c++) cols.push(m.getColumnName(c));
  var o2 = new FileOutputStream(OUT + "/" + TABLES[t] + ".tsv");
  o2.write(JStr.valueOf(cols.join("\t") + "\n").getBytes("UTF-8"));
  var k = 0;
  while (r.next()) {
    var row = []; for (var c = 1; c <= nc; c++) row.push(s(r.getString(c)));
    o2.write(JStr.valueOf(row.join("\t") + "\n").getBytes("UTF-8")); k++;
  }
  o2.close(); st2.close();
  print(TABLES[t] + " rows = " + k);
}
conn.close();
