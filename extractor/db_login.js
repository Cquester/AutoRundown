// AutoRundown extractor - can this PC log in to the VM's database?
// vm_pipeline.py runs it once the database's port answers: the port alone
// proves little (Transbase's multiplexer answers on 2054 / 2034 and hands
// the login on to a kernel on another port, which a firewall can block).
// Prints one line: "LOGIN OK" or "LOGIN FAIL <the driver's error>".
//   AR_HOST, AR_PORT, AR_DB   as for pull.js
//   AR_USER   tbadmin for WIS (pull.js), tbuser for EPC's SPECIALFILES (pull_epc.js)
var System = Java.type("java.lang.System");
function env(k, d){ var v = System.getenv(k); return (v==null||v=="") ? d : v; }
var url = "jdbc:transbase://"+env("AR_HOST","192.168.56.101")+":"+env("AR_PORT","2054")+"/"+env("AR_DB","wisnet");
Java.type("java.lang.Class").forName("transbase.jdbc.Driver");
var DriverManager = Java.type("java.sql.DriverManager");
DriverManager.setLoginTimeout(30);
try {
  var c = DriverManager.getConnection(url, env("AR_USER", "tbadmin"), "");
  c.close();
  print("LOGIN OK " + url);
} catch (e) {
  var lines = String(e).split("\n").filter(function(l){ return /Error|exception/i.test(l); });
  print("LOGIN FAIL " + (lines.join(" | ") || String(e)).replace(/\s+/g, " "));
}
