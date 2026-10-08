// AutoRundown extractor - APPLICABILITY pull (config-driven, body-free & fast).
// Captures the document<->model linkage that the body pull does NOT:
//   W_Baumusterzeile  -> the model catalogue (Baumuster series + digits +
//                        production date range) keyed by BMZeilenNr.
//   W_Dok2Reihe       -> DokNrID -> ReihenID, where ReihenID == BMZeilenNr.
// Together these let parse.py stamp every record with the models and model
// years it applies to (e.g. a 1987 190E 2.3-16 = Baumuster 201.034), which is
// exactly what a "does this apply to my car / before 19xx" filter needs.
//
// Neither table has a SprachCode, so this pull is language-independent: one run
// serves every -Lang. Small output (a few MB), so it runs in a single pass.
//
// Config via environment (set by extract.ps1):
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
  var w=new FileWriter(OUT+"/pull_applic.log",true); w.write("["+ts+"] "+msg+"\n"); w.close();
}
function s(v){ return v==null ? "" : (""+v).replace(/[\t\r\n]/g," "); }

var url = "jdbc:transbase://"+HOST+":"+PORT+"/"+DB;
log("APPLIC pull start url="+url+" out="+OUT);
var conn = DriverManager.getConnection(url, "tbadmin", "");
conn.setAutoCommit(true);

// 1) model catalogue -------------------------------------------------------
var bz = new FileOutputStream(OUT+"/baumusterzeilen.tsv", false);
function bw(x){ bz.write(JStr.valueOf(x).getBytes("UTF-8")); }
bw("BMZeilenNr\tBMReihe\tBM4\tBM5\tBM6\tBMProdVon\tBMProdBis\n");
var st = conn.createStatement(); st.setFetchSize(2000);
var rs = st.executeQuery("SELECT BMZeilenNr,BMReihe,BM4,BM5,BM6,BMProdVon,BMProdBis FROM W_Baumusterzeile");
var nbz=0;
while(rs.next()){
  bw(rs.getInt(1)+"\t"+s(rs.getString(2))+"\t"+s(rs.getString(3))+"\t"+s(rs.getString(4))+"\t"+
     s(rs.getString(5))+"\t"+s(rs.getString(6))+"\t"+s(rs.getString(7))+"\n");
  nbz++;
}
rs.close(); st.close(); bz.flush(); bz.close();
log("W_Baumusterzeile rows = "+nbz);

// 2) doc -> model line linkage --------------------------------------------
var d2 = new BufferedOutputStream(new FileOutputStream(OUT+"/dok2reihe.tsv", false));
function dw(x){ d2.write(JStr.valueOf(x).getBytes("UTF-8")); }
dw("DokNrID\tReihenNr\tReihenID\n");
var st2 = conn.createStatement(); st2.setFetchSize(5000);
var rs2 = st2.executeQuery("SELECT DokNrID,ReihenNr,ReihenID FROM W_Dok2Reihe");
var nd2=0;
while(rs2.next()){
  dw(rs2.getInt(1)+"\t"+rs2.getInt(2)+"\t"+rs2.getInt(3)+"\n");
  nd2++;
  if(nd2%100000===0){ d2.flush(); log("  dok2reihe "+nd2+"..."); }
}
rs2.close(); st2.close(); d2.flush(); d2.close();
log("W_Dok2Reihe rows = "+nd2);

conn.close();
log("APPLIC pull done: baumusterzeilen="+nbz+" dok2reihe="+nd2);
