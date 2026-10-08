// Probe EPC databases (read-only): list tables and columns of each database
// named in AR_DBS, on AR_HOST:AR_PORT (2034 = EPC). Writes
// <AR_OUT>/<db>.tables.tsv and <db>.columns.tsv. SELECT only.
var System = Java.type("java.lang.System");
var DriverManager = Java.type("java.sql.DriverManager");
var FileWriter = Java.type("java.io.FileWriter");
var File = Java.type("java.io.File");
Java.type("java.lang.Class").forName("transbase.jdbc.Driver");
function env(k, d){ var v = System.getenv(k); return (v==null||v=="") ? d : v; }
var HOST = env("AR_HOST", "192.168.56.101"), PORT = env("AR_PORT", "2034");
var OUT = env("AR_OUT", ".").replace(/\\/g, "/");
var DBS = env("AR_DBS", "MBNA_DCNA").split(",");
new File(OUT).mkdirs();

function dump(conn, sql, cols, path) {
  var st = conn.createStatement(); var rs = st.executeQuery(sql);
  var w = new FileWriter(path); w.write(cols.join("\t") + "\n"); var n = 0;
  while (rs.next()) {
    var row = [];
    for (var i = 1; i <= cols.length; i++) { var v = rs.getString(i); row.push(v === null ? "" : (""+v).replace(/[\t\r\n]/g, " ")); }
    w.write(row.join("\t") + "\n"); n++;
  }
  w.close(); rs.close(); st.close(); return n;
}

for (var i = 0; i < DBS.length; i++) {
  var db = DBS[i], url = "jdbc:transbase://" + HOST + ":" + PORT + "/" + db;
  try {
    var conn = DriverManager.getConnection(url, env("AR_USER", "tbuser"), "");
    conn.setReadOnly(true);
    var nt = dump(conn, "SELECT tname, ttype FROM systable WHERE tname NOT LIKE 'sys%' ORDER BY tname",
                  ["tname", "ttype"], OUT + "/" + db + ".tables.tsv");
    var nc = dump(conn, "SELECT t.tname, c.cname, c.ctype FROM systable t, syscolumn c WHERE c.tsegno = t.segno AND t.tname NOT LIKE 'sys%' ORDER BY t.tname, c.cpos",
                  ["tname", "cname", "ctype"], OUT + "/" + db + ".columns.tsv");
    print("OK   " + db + ": " + nt + " tables, " + nc + " columns");
    conn.close();
  } catch (e) {
    var msg = "" + e; try { msg = e.getMessage ? ("" + e.getMessage()) : msg; } catch (x) {}
    print("FAIL " + db + ": " + msg.replace(/\s+/g, " ").slice(0, 400));
  }
}
