// AutoRundown EPC extractor (Mercedes EPC parts catalogue -> records.jsonl).
//
// Reads the user's own licensed EPC data from the ALLTEXT Transbase database
// in their Mercedes VM and emits the same records.jsonl + manifest.json corpus
// the `records` importer already ingests (schema in extractor/IMPORT_SPEC.md),
// filed under the model series so EPC parts land on the same vehicle as WIS.
//
// STATUS: proven end to end on one catalog (001 = W115): 7,732 parts, filed
// under MODEL 115, FTS-searchable by part name and exact part number. Open
// refinements (verify/add next, see docs/notes/epc-source.md): subgroup
// completeness (enumerate from BM_PARTS2_V/BM_NPG_V, not just BM_SUBGRP_V),
// illustration images (BM_NPG_V -> IMAGE_V), SA/option parts (SA_PARTS_V),
// part-number formatting and the QUANTBM per-model quantity decode, all catalogs.
//
// How the DB login works (decompiled from the EPC server classes): open
// SPECIALFILES as tbuser (empty password), read PWORD from PWORD_V, ROT13 it
// (EPCConfigManager.decyptPW is a plain Caesar-13), then open ALLTEXT/COMM as
// tbuser with that password. The password is never printed.
//
// Run with the EWA bundled JRE (Nashorn), e.g.:
//   jjs.exe pull_epc.js
// Env: AR_HOST (192.168.56.101), AR_PORT (2034), AR_LANG (E), AR_CATNUM (001),
//      AR_OUT (output dir), AR_TBJDBC (path to tbjdbc.jar).

var System=Java.type("java.lang.System"), File=Java.type("java.io.File");
var FileWriter=Java.type("java.io.FileWriter"), BufferedWriter=Java.type("java.io.BufferedWriter");
var URL=Java.type("java.net.URL"), URLClassLoader=Java.type("java.net.URLClassLoader");
var Properties=Java.type("java.util.Properties"), Clazz=Java.type("java.lang.Class");
var ClassLoader=Java.type("java.lang.ClassLoader");
function env(k,d){var v=System.getenv(k);return(v==null||v=="")?d:v;}

var HOST=env("AR_HOST","192.168.56.101"), PORT=env("AR_PORT","2034");
var LANG=env("AR_LANG","E");                         // EPC single-char language code
var CATNUM=env("AR_CATNUM","001");                   // one catalog for now; "all" later
var OUT=env("AR_OUT", "epc_extract");
new File(OUT).mkdirs();
function url(db){return "jdbc:transbase://"+HOST+":"+PORT+"/"+db;}
var JAR=env("AR_TBJDBC","tbjdbc.jar");
var cl=new URLClassLoader([new File(JAR).toURI().toURL()], ClassLoader.getSystemClassLoader());
var drv=Clazz.forName("transbase.jdbc.Driver",true,cl).newInstance();
function openDb(db,user,pw){var p=new Properties();p.setProperty("user",user);p.setProperty("password",pw);p.setProperty("characterEncoding","UTF-8");var c=drv.connect(url(db),p);if(c==null)throw "null connection "+db;return c;}
function rot13(s){ return s.replace(/[A-Za-z]/g, function(c){ var b=c<='Z'?65:97; return String.fromCharCode((c.charCodeAt(0)-b+13)%26+b); }); }
function trim(s){ return s==null?"":(""+s).replace(/^\s+|\s+$/g,""); }
function esc(s){ return (""+s).replace(/'/g,"''"); }

// connect: read + ROT13 the catalogue password from SPECIALFILES, open ALLTEXT
var sf=openDb("SPECIALFILES","tbuser","");
var r=sf.createStatement().executeQuery("select pword from PWORD_V"); r.next(); var pw=rot13(r.getString(1)); r.close(); sf.close();
var at=openDb("ALLTEXT","tbuser",pw);
print("ALLTEXT open. catnum="+CATNUM+" lang="+LANG+" out="+OUT);
function query(sql){ var s=at.createStatement(); return {s:s, rs:s.executeQuery(sql)}; }

// noun dictionary (NOUNIDX -> part name) for this language
var nouns={}, nc=0;
(function(){ var h=query("select NOUNIDX, NOUN from part_nouns_v where LANG='"+esc(LANG)+"'");
  while(h.rs.next()){ nouns[trim(h.rs.getString(1))]=trim(h.rs.getString(2)); nc++; } h.rs.close(); h.s.close(); })();
print("loaded "+nc+" nouns");

// catalog -> series + baumuster + sales designations
var seriesSet={}, bmSet={}, sales=[];
(function(){ var h=query("select type, subbm1, salesdes from models_v where catnum='"+esc(CATNUM)+"'");
  while(h.rs.next()){ var type=trim(h.rs.getString(1)), sub1=trim(h.rs.getString(2)), sd=trim(h.rs.getString(3));
    if(type){ seriesSet[type]=1; if(sub1) bmSet[type+"."+sub1]=1; }
    if(sd && sales.indexOf(sd)<0) sales.push(sd);
  } h.rs.close(); h.s.close(); })();
var model_series=Object.keys(seriesSet), baumuster=Object.keys(bmSet);
print("catalog "+CATNUM+" -> series "+model_series.join(",")+" ; "+baumuster.length+" baumuster");

// groups (number + text)
var groups=[];
(function(){ var h=query("select GROUPNUM, TEXT from bm_group_v join BM_DICTIONARY_V using (DESCIDX) where LANG='"+esc(LANG)+"' and CATNUM='"+esc(CATNUM)+"' order by GROUPNUM");
  while(h.rs.next()) groups.push({g:trim(h.rs.getString(1)), text:trim(h.rs.getString(2))}); h.rs.close(); h.s.close(); })();
print("groups: "+groups.length);

var bw=new BufferedWriter(new FileWriter(OUT+"/records.jsonl"));
var dokId=0, recCount=0, partRows=0, emptyRecs=0;

function partsFor(g, sg){
  var sql="select SEQNO, CALLOUT, PARTTYPE, PARTNUM, QUANTBM, NOUNIDX, PARTREM, "
    +"coalesce((select description from part_descs_v where descidx=a.descidx and lang='"+esc(LANG)+"'),"
    +" (select description from part_descs_v where descidx=a.descidx and lang='E')) as DESCRIPTION "
    +"from BM_PARTS2_V a where CATNUM='"+esc(CATNUM)+"' and GROUPNUM='"+esc(g)+"' and SUBGRP='"+esc(sg)+"' order by SEQNUM";
  var rows=[], h=query(sql);
  while(h.rs.next()){
    var pt=trim(h.rs.getString("PARTTYPE")), pn=trim(h.rs.getString("PARTNUM"));
    if((pt+pn).indexOf("XXXXX")>=0 || (pt+pn).indexOf("YYYYY")>=0) continue; // known placeholders the server skips
    var name=nouns[trim(h.rs.getString("NOUNIDX"))]||"";
    rows.push({callout:trim(h.rs.getString("CALLOUT")), partno:(pt+" "+pn).replace(/\s+/g," ").replace(/^\s|\s$/g,""),
               name:name, desc:trim(h.rs.getString("DESCRIPTION")), rem:trim(h.rs.getString("PARTREM"))});
    partRows++;
  }
  h.rs.close(); h.s.close();
  return rows;
}

for (var gi=0; gi<groups.length; gi++){
  var g=groups[gi];
  var subs=[], h=query("select SUBGRP, TEXT from BM_SUBGRP_V where CATNUM='"+esc(CATNUM)+"' and GROUPNUM='"+esc(g.g)+"' and LANG='"+esc(LANG)+"' order by SUBGRP");
  while(h.rs.next()) subs.push({sg:trim(h.rs.getString(1)), text:trim(h.rs.getString(2))}); h.rs.close(); h.s.close();
  if(subs.length===0) subs.push({sg:"", text:""});
  for (var si=0; si<subs.length; si++){
    var sg=subs[si];
    var parts = sg.sg ? partsFor(g.g, sg.sg) : [];
    dokId++;
    var title="Parts: group "+g.g+(g.text?" "+g.text:"")+(sg.sg?(" / subgroup "+sg.sg+(sg.text?" "+sg.text:"")):"");
    var lines=[title];
    if(sales.length) lines.push("Sales designations: "+sales.join(", "));
    lines.push("");
    if(parts.length){
      lines.push("Callout | Part number | Name");
      for (var pi=0; pi<parts.length; pi++){ var p=parts[pi]; var nm=(p.name||p.desc||"").trim();
        lines.push((p.callout||"-")+" | "+p.partno+" | "+nm+(p.rem?(" ("+p.rem+")"):"")); }
    } else { lines.push("(no parts listed)"); emptyRecs++; }
    var rec={
      dok_id: dokId, lang: "en", title: title, body_text: lines.join("\n"), body_format: "sids",
      model_series: model_series, baumuster: baumuster,
      applicability_notes: model_series.map(function(s){return "MODEL "+s;}).concat(baumuster.map(function(b){return "MODEL "+b;})),
      cross_refs: [], image_refs: [], figures: [], image_files: [],
      source_type: "mercedes-epc", catnum: CATNUM, group: g.g, subgroup: sg.sg
    };
    bw.write(JSON.stringify(rec)); bw.write("\n"); recCount++;
    if(recCount%200===0) print("  "+recCount+" records, "+partRows+" part rows...");
  }
}
bw.close();
var manifest={schema_version:"1.3", source_type:"mercedes-epc", source_id:HOST+"/ALLTEXT",
  database:"ALLTEXT", language:{iso:"en"}, catnum:CATNUM, record_count:recCount, part_rows:partRows,
  empty_body_count:emptyRecs, body_format_counts:{sids:recCount}, records_file:"records.jsonl",
  extracted_at:(new Date()).toISOString()};
var mw=new FileWriter(OUT+"/manifest.json"); mw.write(JSON.stringify(manifest,null,2)); mw.close();
at.close();
print("\nDONE: "+recCount+" records, "+partRows+" part rows -> "+OUT+"/records.jsonl  (series "+model_series.join(",")+")");
