// List the languages available in a WIS database, with document counts.
// Config via env: AR_HOST, AR_PORT (2054), AR_DB (wisnet).
var System = Java.type("java.lang.System");
var DriverManager = Java.type("java.sql.DriverManager");
Java.type("java.lang.Class").forName("transbase.jdbc.Driver");
function env(k, d){ var v = System.getenv(k); return (v==null||v=="") ? d : v; }
var url = "jdbc:transbase://"+env("AR_HOST","192.168.56.101")+":"+env("AR_PORT","2054")+"/"+env("AR_DB","wisnet");
var c = DriverManager.getConnection(url, "tbadmin", "");
var s = c.createStatement();

// SprachCode -> ISO (+ optional subtag), from the DB's own lookup table
var iso = {};
try {
  var ri = s.executeQuery("SELECT SprachCode, ISOSprachCode, Subtag FROM W_SprachCode2ISOSprachCode");
  while (ri.next()) {
    var code = ri.getInt(1), lang = (ri.getString(2)||"").trim(), sub = (ri.getString(3)||"").trim();
    iso[code] = sub ? (lang + "-" + sub) : lang;
  }
  ri.close();
} catch (e) { print("(no ISO table: " + e + ")"); }

// counts per language
print("SprachCode\tlang\tdocs_with_title");
var r = s.executeQuery("SELECT SprachCode, COUNT(*) FROM W_SprachDokumente WHERE DokTitel IS NOT NULL GROUP BY SprachCode ORDER BY SprachCode");
var rows = [];
while (r.next()) { rows.push({code: r.getInt(1), n: r.getInt(2)}); }
r.close(); s.close(); c.close();
rows.sort(function(a, b){ return b.n - a.n; });   // busiest first (client-side)
for (var i = 0; i < rows.length; i++) {
  print(rows[i].code + "\t" + (iso[rows[i].code] || ("lang" + rows[i].code)) + "\t" + rows[i].n);
}
