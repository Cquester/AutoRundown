// Dump the Transbase WIS schema over JDBC (read-only). jjs + tbjdbc.jar.
var DriverManager = Java.type("java.sql.DriverManager");
var FileWriter = Java.type("java.io.FileWriter");
Java.type("java.lang.Class").forName("transbase.jdbc.Driver");

var URL = "jdbc:transbase://192.168.56.101:2054/wisnet";
var USER = "tbadmin";
var OUT = (java.lang.System.getenv("AR_OUT") || "./schema");

var conn = DriverManager.getConnection(URL, USER, "");
print("connected: " + URL);

function dump(sql, cols, path) {
  var st = conn.createStatement();
  var rs = st.executeQuery(sql);
  var w = new FileWriter(path);
  w.write(cols.join("\t") + "\n");
  var n = 0;
  while (rs.next()) {
    var row = [];
    for (var i = 1; i <= cols.length; i++) {
      var v = rs.getString(i);
      row.push(v === null ? "" : v.replace(/[\t\r\n]/g, " "));
    }
    w.write(row.join("\t") + "\n"); n++;
  }
  w.close(); rs.close(); st.close();
  print("  " + path.replace(/.*\//,"") + "  (" + n + " rows)");
}

dump("SELECT tname, ttype, creator FROM systable ORDER BY tname",
     ["tname","ttype","creator"], OUT + "/wis.tables.tsv");
dump("SELECT tname, cname, ctype, clength, cpos FROM syscolumn ORDER BY tname, cpos",
     ["tname","cname","ctype","clength","cpos"], OUT + "/wis.columns.tsv");
conn.close();
print("done");
