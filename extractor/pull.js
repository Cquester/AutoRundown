// AutoRundown extractor - PULL stage (config-driven).
// Reads a WIS Transbase DB over JDBC and streams every document body for one
// language into a length-framed pack, resumable via a DokID watermark.
//
// Configuration comes from environment variables (set by extract.ps1):
//   AR_HOST  guest DB host           (e.g. 192.168.56.101)
//   AR_PORT  DB port                 (default 2054 = WIS)
//   AR_DB    database name           (default wisnet)
//   AR_OUT   output directory        (created if missing)
//   AR_LANG  SprachCode to extract   (default 2 = English)
//   AR_JAR   path to tbjdbc.jar      (on the classpath already; informational)
//   AR_MAX_S stop (checkpointed, exit 4) after this many seconds; 0 = never.
//            The database dies ~10 min into a session, so the pipeline
//            restarts the VM and resumes before that.
var System = Java.type("java.lang.System");
var DriverManager = Java.type("java.sql.DriverManager");
var JStr = Java.type("java.lang.String");
var File = Java.type("java.io.File");
var FileOutputStream = Java.type("java.io.FileOutputStream");
var FileWriter = Java.type("java.io.FileWriter");
var BufferedOutputStream = Java.type("java.io.BufferedOutputStream");
var DataOutputStream = Java.type("java.io.DataOutputStream");
var Files = Java.type("java.nio.file.Files");
var Paths = Java.type("java.nio.file.Paths");
Java.type("java.lang.Class").forName("transbase.jdbc.Driver");

function env(k, d){ var v = System.getenv(k); return (v==null||v=="") ? d : v; }
var HOST = env("AR_HOST", "192.168.56.101");
var PORT = env("AR_PORT", "2054");
var DB   = env("AR_DB",   "wisnet");
var OUT  = env("AR_OUT",  ".").replace(/\\/g, "/");
var LANG = parseInt(env("AR_LANG", "2"), 10);
var CHECKPOINT = 500;
var MAX_S = parseInt(env("AR_MAX_S", "0"), 10);
var T0 = System.currentTimeMillis();

new File(OUT).mkdirs();
function readWatermark(){
  var f = new File(OUT+"/watermark.txt");
  if(!f.exists()) return -1;
  var s = new JStr(Files.readAllBytes(Paths.get(OUT+"/watermark.txt")),"UTF-8").trim();
  return s.length()===0 ? -1 : parseInt(s,10);
}
function writeWatermark(id){ var w=new FileWriter(OUT+"/watermark.txt",false); w.write(""+id); w.close(); }
function log(msg){
  var ts=new (Java.type("java.text.SimpleDateFormat"))("HH:mm:ss").format(new (Java.type("java.util.Date"))());
  print("["+ts+"] "+msg);
  var w=new FileWriter(OUT+"/pull.log",true); w.write("["+ts+"] "+msg+"\n"); w.close();
}

var wm = readWatermark();
var fresh = (wm < 0);
var url = "jdbc:transbase://"+HOST+":"+PORT+"/"+DB;
log("PULL start url="+url+" lang="+LANG+" out="+OUT+" watermark="+wm+(fresh?" (fresh)":" (resume)"));

var conn = DriverManager.getConnection(url, "tbadmin", "");
conn.setAutoCommit(true);

if(fresh){
  var cs=conn.createStatement();
  var cr=cs.executeQuery("SELECT COUNT(*) FROM W_SprachDokumente WHERE SprachCode="+LANG+" AND DokTitel IS NOT NULL");
  cr.next(); log("total docs for lang "+LANG+" = "+cr.getInt(1)); cr.close(); cs.close();
}

var pack=new DataOutputStream(new BufferedOutputStream(new FileOutputStream(OUT+"/bodies.pack", !fresh)));
var meta=new FileOutputStream(OUT+"/metadata.tsv", !fresh);
var figs=new FileOutputStream(OUT+"/figures.tsv", !fresh);
function mw(s){ meta.write(JStr.valueOf(s).getBytes("UTF-8")); }
function fw(s){ figs.write(JStr.valueOf(s).getBytes("UTF-8")); }
if(fresh){
  mw("DokID\tDokNrID\tDokTypCode\tFzgArtCodes\tKurzgueltigkeitID\tCluster\tBlobBytes\tDokTitel\n");
  fw("DokID\tGraphikID\tGraphikNr\tGraphikKlasse\tCluster\n");
}

// A failed query here almost always means the DB connection has gone (the WIS
// server drops it under load or after idling). The old behaviour was to log a
// WARN and carry on -- but with the graphics lookup failed, no body is even
// attempted, so every remaining document was written to metadata.tsv with
// BlobBytes=0 and then checkpointed past: silently recorded as bodyless,
// indistinguishable from a real figure-only stub. So stop instead. Every
// pack/metadata write completes before the next DB call, so flushing and
// checkpointing at the last good document leaves nothing half-written, and a
// re-run resumes from exactly there.
var lastDok=wm;
function abort(dokid, what, e){
  pack.flush(); figs.flush(); meta.flush();
  writeWatermark(lastDok);
  log("ABORT "+what+" query failed at DokID "+dokid+": "+e);
  log("ABORT checkpointed at DokID "+lastDok+" -- re-run to resume from there");
  System.exit(2);
}

var psAllGfx=conn.prepareStatement("SELECT GraphikID, GraphikNr, GraphikKlasse, Cluster FROM ID2Cluster WHERE DokID=?");
var bodyStmts={};
function bodyStmt(cl){
  if(!bodyStmts[cl]) bodyStmts[cl]=conn.prepareStatement("SELECT DokGraphikBlob FROM ID2Blob_"+cl+" WHERE DokID=? AND GraphikID=0");
  return bodyStmts[cl];
}

var main=conn.createStatement(); main.setFetchSize(2000);
var rs=main.executeQuery(
  "SELECT DokID, DokNrID, DokTypCode, FzgArtCodes, KurzgueltigkeitID, DokTitel "+
  "FROM W_SprachDokumente WHERE SprachCode="+LANG+" AND DokTitel IS NOT NULL AND DokID > "+wm+" ORDER BY DokID");

var got=0, nobody=0, sinceCk=0;
try {
while(rs.next()){
  var dokid=rs.getInt(1), nrid=rs.getInt(2), typ=rs.getInt(3);
  var fzg=rs.getString(4), kg=rs.getInt(5), title=rs.getString(6);
  var bodyCluster=-1, figLines=[];
  try{
    psAllGfx.setInt(1,dokid); var rg=psAllGfx.executeQuery();
    while(rg.next()){
      var gid=rg.getInt(1), gnr=rg.getString(2), gk=rg.getInt(3), cl=rg.getInt(4);
      if(gid===0) bodyCluster=cl; else figLines.push(dokid+"\t"+gid+"\t"+(gnr||"")+"\t"+gk+"\t"+cl+"\n");
    }
    rg.close();
  }catch(e){ abort(dokid, "graphics", e); }
  var blen=0;
  if(bodyCluster>=0){
    try{
      var bs=bodyStmt(bodyCluster); bs.setInt(1,dokid); var rb=bs.executeQuery();
      if(rb.next()){ var b=rb.getBytes(1); if(b!=null && b.length>0){ blen=b.length; pack.writeInt(dokid); pack.writeInt(blen); pack.write(b,0,blen); } }
      rb.close();
    }catch(e){ abort(dokid, "body (cluster "+bodyCluster+")", e); }
  }
  if(blen===0) nobody++;
  mw(dokid+"\t"+nrid+"\t"+typ+"\t"+fzg+"\t"+kg+"\t"+bodyCluster+"\t"+blen+"\t"+(title==null?"":title.replace(/[\t\r\n]/g," "))+"\n");
  for(var i=0;i<figLines.length;i++) fw(figLines[i]);
  got++; sinceCk++; lastDok=dokid;
  if(sinceCk>=CHECKPOINT){ pack.flush(); meta.flush(); figs.flush(); writeWatermark(lastDok); sinceCk=0; }
  if(got%5000===0){ log("progress: "+got+" pulled, "+nobody+" no-body, at DokID "+dokid); Java.type("java.lang.Thread").sleep(300); }
  if(MAX_S && System.currentTimeMillis()-T0 > MAX_S*1000){
    pack.flush(); meta.flush(); figs.flush(); writeWatermark(lastDok);
    log("SESSION limit "+MAX_S+" s: pulled="+got+", checkpointed at DokID "+lastDok+" -- resumes from there");
    System.exit(4);
  }
}
} catch(e) {
  // the document list itself died -- the connection dropped between rows
  abort("(next after "+lastDok+")", "document list", e);
}
rs.close(); main.close();
pack.flush(); pack.close(); meta.flush(); meta.close(); figs.flush(); figs.close();
writeWatermark(lastDok); conn.close();
log("PULL done: pulled="+got+" no_body="+nobody+" lastDokID="+lastDok);
