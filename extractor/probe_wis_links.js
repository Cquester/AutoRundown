// AutoRundown extractor - read-only probe of the WIS link tables for a few
// documents: wiring-diagram component positions (DXF, AW_DXFReferenz,
// DXFLegende, DXFLegendePos, AW_SVGReferenz), cross-references (DokVerweis,
// W_Dokumente), and English row counts for the SA-code and damage-code
// tables. Answers "can we line these up with what we already extracted?"
//
// SELECT only.  AR_HOST AR_PORT AR_DB AR_OUT as the other pulls;
// AR_DOKS = comma list of DokIDs (wiring diagrams), AR_XREF = one DokID.
var System = Java.type("java.lang.System");
var DriverManager = Java.type("java.sql.DriverManager");
var JStr = Java.type("java.lang.String");
var FileOutputStream = Java.type("java.io.FileOutputStream");
Java.type("java.lang.Class").forName("transbase.jdbc.Driver");

function env(k, d){ var v = System.getenv(k); return (v==null||v=="") ? d : v; }
var OUT = env("AR_OUT", ".").replace(/\\/g, "/");
var DOKS = env("AR_DOKS", "25131415,25131417");
var XREF = env("AR_XREF", "1002304");
var conn = DriverManager.getConnection("jdbc:transbase://" + env("AR_HOST", "192.168.56.101") + ":" +
    env("AR_PORT", "2054") + "/" + env("AR_DB", "wisnet"), "tbadmin", "");
var o = new FileOutputStream(OUT + "/links_probe.txt", false);
function w(t){ o.write(JStr.valueOf(t + "\n").getBytes("UTF-8")); print(t); }
function s(v){ return v==null ? "" : (""+v).replace(/[\t\r\n]/g," ").trim(); }

function q(sql, max){
  w("== " + sql);
  try {
    var st = conn.createStatement(); st.setMaxRows(max || 40);
    var r = st.executeQuery(sql), m = r.getMetaData(), n = m.getColumnCount(), k = 0;
    var cols = []; for (var c = 1; c <= n; c++) cols.push(m.getColumnName(c));
    w("  " + cols.join(" | "));
    while (r.next()) {
      var row = []; for (var c = 1; c <= n; c++) row.push(s(r.getString(c)).substring(0, 100));
      w("  " + row.join(" | ")); k++;
    }
    st.close(); w("  (" + k + " rows shown)");
  } catch (e) { w("  ! " + e); }
}

q("SELECT * FROM DXF WHERE DokID IN (" + DOKS + ")");
q("SELECT COUNT(*), DokID FROM AW_DXFReferenz WHERE DokID IN (" + DOKS + ") GROUP BY DokID");
q("SELECT * FROM AW_DXFReferenz WHERE DokID IN (" + DOKS + ")", 25);
q("SELECT COUNT(*), DokID FROM DXFLegendePos WHERE DokID IN (" + DOKS + ") GROUP BY DokID");
q("SELECT * FROM DXFLegendePos WHERE DokID IN (" + DOKS + ")", 25);
q("SELECT * FROM DXFLegende WHERE DokID IN (" + DOKS + ")", 25);
q("SELECT * FROM AW_SVGReferenz WHERE DokID IN (" + DOKS + ")", 15);
q("SELECT * FROM DokVerweis WHERE DokID = " + XREF);
q("SELECT d.* FROM DokVerweis v, W_Dokumente d WHERE v.DokID = " + XREF + " AND d.DokNrID = v.DokNrIDZu");
q("SELECT * FROM W_Dokumente WHERE DokNrID IN (SELECT DokNrIDZu FROM DokVerweis WHERE DokID = " + XREF + ")");
q("SELECT COUNT(*) FROM SACode WHERE SprachCode = 2");
q("SELECT * FROM SACode WHERE SprachCode = 2", 8);
q("SELECT COUNT(*) FROM SANummer WHERE SprachCode = 2");
q("SELECT COUNT(*) FROM SSTeilID WHERE SprachCode = 2");
q("SELECT COUNT(*) FROM AW_AssystFuellmengen WHERE SprachCode = 2");
q("SELECT * FROM AW_AssystFuellmengen WHERE SprachCode = 2", 8);
q("SELECT COUNT(*) FROM ASRA_Arbeitsposition WHERE SprachAbk = '02'");
q("SELECT * FROM ASRA_Arbeitsposition WHERE SprachAbk = '02'", 6);
q("SELECT DISTINCT SprachAbk FROM ASRA_Arbeitsposition", 40);
o.close(); conn.close();
