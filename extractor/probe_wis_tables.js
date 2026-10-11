// AutoRundown extractor - read-only survey of every table in the WIS database.
// For each user table: its columns (from the driver's metadata), a row count
// (skipped for the ID2Blob_* graphic stores, which are large and known), and
// the first 5 rows. Answers "is there a table we don't pull?" -- e.g. parts,
// quantities, or what the picture-menu hotspot codes ("5441R") point to.
//
// SELECT only; writes nothing to the database.
//
//   AR_HOST guest DB host   AR_PORT port   AR_DB database   AR_OUT output dir
//
// Output: AR_OUT/tables.tsv (table, columns, rows) and AR_OUT/samples.txt.
var System = Java.type("java.lang.System");
var DriverManager = Java.type("java.sql.DriverManager");
var JStr = Java.type("java.lang.String");
var File = Java.type("java.io.File");
var FileOutputStream = Java.type("java.io.FileOutputStream");
Java.type("java.lang.Class").forName("transbase.jdbc.Driver");

function env(k, d){ var v = System.getenv(k); return (v==null||v=="") ? d : v; }
var HOST = env("AR_HOST", "192.168.56.101");
var PORT = env("AR_PORT", "2054");
var DB   = env("AR_DB",   "wisnet");
var OUT  = env("AR_OUT",  ".").replace(/\\/g, "/");
new File(OUT).mkdirs();

function s(v){ return v==null ? "" : (""+v).replace(/[\t\r\n]/g," ").trim(); }
function writer(path){
  var o = new FileOutputStream(path, false);
  return { w: function(t){ o.write(JStr.valueOf(t).getBytes("UTF-8")); }, close: function(){ o.close(); } };
}

var conn = DriverManager.getConnection("jdbc:transbase://"+HOST+":"+PORT+"/"+DB, "tbadmin", "");
conn.setAutoCommit(true);
var md = conn.getMetaData();
var rs = md.getTables(null, null, "%", ["TABLE"]);
var names = [];
while (rs.next()) names.push(s(rs.getString("TABLE_NAME")));
rs.close();
names.sort();
print("tables: " + names.length);

var tsv = writer(OUT + "/tables.tsv"), smp = writer(OUT + "/samples.txt");
tsv.w("table\trows\tcolumns\n");
for (var i = 0; i < names.length; i++) {
  var t = names[i];
  try {
    var cols = [];
    var cr = md.getColumns(null, null, t, "%");
    while (cr.next()) cols.push(s(cr.getString("COLUMN_NAME")) + ":" + s(cr.getString("TYPE_NAME")));
    cr.close();
    var rows = "";
    if (t.indexOf("ID2Blob") != 0) {
      var st = conn.createStatement();
      var c = st.executeQuery("SELECT COUNT(*) FROM " + t);
      if (c.next()) rows = s(c.getString(1));
      st.close();
    }
    tsv.w(t + "\t" + rows + "\t" + cols.join(", ") + "\n");
    smp.w("== " + t + " (" + rows + " rows)\n");
    if (t.indexOf("ID2Blob") != 0) {
      var st2 = conn.createStatement(); st2.setMaxRows(5);
      var r = st2.executeQuery("SELECT * FROM " + t);
      var m = r.getMetaData(), n = m.getColumnCount();
      while (r.next()) {
        var row = [];
        for (var k = 1; k <= n; k++) {
          var ty = m.getColumnTypeName(k).toLowerCase();
          row.push(ty.indexOf("blob") >= 0 ? "<blob>" : s(r.getString(k)).substring(0, 120));
        }
        smp.w("  " + row.join(" | ") + "\n");
      }
      st2.close();
    }
    print((i + 1) + "/" + names.length + " " + t + " " + rows);
  } catch (e) {
    tsv.w(t + "\tERROR\t" + s(e) + "\n");
    print("! " + t + ": " + e);
  }
}
tsv.close(); smp.close();
conn.close();
print("done");
