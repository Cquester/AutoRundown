// Read-only survey of EPC databases that open normally with AR_USER (tbuser,
// empty password -- the account epc_cfg.xml gives the catalogue). For each DB
// in AR_DBS: base tables, (optionally) row counts, first AR_SAMPLE rows.
// SELECT only. Credential-looking tables (PWORD*, SYS*) are never read.
// AR_TTYPE=V surveys the views instead (in SPECIALFILES the base tables refuse
// tbuser; the granted _V views are what the account reads). AR_COUNT=0 skips
// row counts; AR_SUFFIX is added to the output name (<db><suffix>.survey.txt).
// Findings (2026-10-08): docs/MERCEDES_WIS_EXTRACTION.md section 7.
var System = Java.type("java.lang.System");
var DriverManager = Java.type("java.sql.DriverManager");
var FileWriter = Java.type("java.io.FileWriter");
var File = Java.type("java.io.File");
Java.type("java.lang.Class").forName("transbase.jdbc.Driver");
function env(k, d){ var v = System.getenv(k); return (v==null||v=="") ? d : v; }
var HOST = env("AR_HOST", "192.168.56.101"), PORT = env("AR_PORT", "2034");
var OUT = env("AR_OUT", ".").replace(/\\/g, "/");
var DBS = env("AR_DBS", "SPECIALFILES").split(",");
var SAMPLE = parseInt(env("AR_SAMPLE", "15"));
var COUNT = env("AR_COUNT", "1") == "1";
var SKIP = /^(PWORD|SYS)/i;   // passwords / system catalogue: not read
new File(OUT).mkdirs();
function clean(v){ return v === null ? "" : (""+v).replace(/[\t\r\n]/g, " "); }

for (var i = 0; i < DBS.length; i++) {
  var db = DBS[i];
  var w = new FileWriter(OUT + "/" + db + env("AR_SUFFIX", "") + ".survey.txt");
  try {
    var conn = DriverManager.getConnection("jdbc:transbase://" + HOST + ":" + PORT + "/" + db, env("AR_USER", "tbuser"), "");
    conn.setReadOnly(true);
    var st = conn.createStatement();
    var rs = st.executeQuery("SELECT tname FROM systable WHERE ttype IN ('" + env("AR_TTYPE", "R','r") + "') ORDER BY tname");
    var tables = []; while (rs.next()) tables.push(""+rs.getString(1)); rs.close();
    print("OK   " + db + ": " + tables.length + " base tables");
    for (var t = 0; t < tables.length; t++) {
      var tn = tables[t];
      if (SKIP.test(tn)) { w.write("== " + tn + "  (skipped, not read)\n\n"); continue; }
      try {
        var n = "?";
        if (COUNT) { rs = st.executeQuery("SELECT COUNT(*) FROM " + tn); rs.next(); n = rs.getString(1); rs.close(); }
        w.write("== " + tn + "  rows=" + n + "\n");
        print("  " + tn + " rows=" + n);
        if (SAMPLE > 0) {
          st.setMaxRows(SAMPLE);
          rs = st.executeQuery("SELECT * FROM " + tn);
          var md = rs.getMetaData(), nc = md.getColumnCount(), hdr = [];
          for (var c = 1; c <= nc; c++) hdr.push(md.getColumnName(c));
          w.write(hdr.join("\t") + "\n");
          while (rs.next()) { var row = []; for (var c = 1; c <= nc; c++) row.push(clean(rs.getString(c)).slice(0, 300)); w.write(row.join("\t") + "\n"); }
          rs.close(); st.setMaxRows(0);
        }
        w.write("\n");
      } catch (e) { w.write("== " + tn + "  ERROR " + clean(""+e).slice(0, 300) + "\n\n"); }
    }
    st.close(); conn.close();
  } catch (e) {
    var msg = "" + e; try { msg = "" + e.getMessage(); } catch (x) {}
    print("FAIL " + db + ": " + msg.replace(/\s+/g, " ").slice(0, 300));
    w.write("FAIL " + msg + "\n");
  }
  w.close();
}
