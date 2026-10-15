// AutoRundown EPC extractor (Mercedes EPC parts catalogue -> records.jsonl).
//
// Reads the user's own licensed EPC data from the Transbase databases in their
// Mercedes VM and emits the same records.jsonl + manifest.json corpus the
// `records` importer already ingests (schema in extractor/IMPORT_SPEC.md),
// filed under the model series so EPC parts land on the same vehicle as WIS.
//
// One record per (catalog, group, subgroup) and one per (SA option, subgroup).
// Each part line: callout | part number (spaced + compact) | name | which
// baumuster it fits and how many | supersession and footnote numbers; the
// page ends with the texts of those footnotes ("fn 003: up to engine
// 32946334 (change date 2015-06-11)", decoded by epc_footnotes.js). The subgroup's exploded
// drawings are decoded to PNG under <AR_OUT>/images/epc/ (image_files).
// Schema facts behind all of this: docs/notes/epc-source.md.
//
// How the DB login works (decompiled from the EPC server classes): open
// SPECIALFILES as tbuser (empty password), read PWORD from PWORD_V, ROT13 it
// (EPCConfigManager.decyptPW is a plain Caesar-13), then open ALLTEXT and the
// image databases as tbuser with that password. The password is never printed.
//
// Run with the EWA bundled JRE (Nashorn), e.g.:
//   jjs.exe pull_epc.js
// Env: AR_HOST (192.168.56.101), AR_PORT (2034), AR_LANG (E), AR_TBJDBC (tbjdbc.jar),
//      AR_OUT (output dir, default epc_extract),
//      AR_CATNUM  "001" | "001,002" | "all" (= every passenger-car chassis
//                 catalog: models_v class 1, aggtype FG) | "engines" (class 1,
//                 aggtype M; filed under the engine buckets, "ENGINE 112"),
//                 tokens may be mixed: "all,engines",
//      AR_LIMIT   pull at most this many catalogs not yet done (for a trial),
//      AR_IMAGES  1 (default) / 0,  AR_SA 1 (default) / 0,
//      AR_RESUME  1 = keep finished catalogs in AR_OUT and carry on (default 1),
//      AR_SLEEP_MS pause between subgroups (default 0; the VM and host stay calm).

var System=Java.type("java.lang.System"), File=Java.type("java.io.File"), Thread=Java.type("java.lang.Thread");
var BufferedWriter=Java.type("java.io.BufferedWriter"), OutputStreamWriter=Java.type("java.io.OutputStreamWriter");
var FileOutputStream=Java.type("java.io.FileOutputStream"), ByteArrayOutputStream=Java.type("java.io.ByteArrayOutputStream");
var Files=Java.type("java.nio.file.Files"), Paths=Java.type("java.nio.file.Paths"), StandardCharsets=Java.type("java.nio.charset.StandardCharsets");
var URLClassLoader=Java.type("java.net.URLClassLoader"), Properties=Java.type("java.util.Properties");
var Clazz=Java.type("java.lang.Class"), ClassLoader=Java.type("java.lang.ClassLoader");
var ImageIO=Java.type("javax.imageio.ImageIO");
var ByteArr=Java.type("byte[]");
function env(k,d){var v=System.getenv(k);return(v==null||v=="")?d:v;}

var HOST=env("AR_HOST","192.168.56.101"), PORT=env("AR_PORT","2034");
var LANG=env("AR_LANG","E");                         // EPC single-char language code
var CATNUM=env("AR_CATNUM","001");
var OUT=env("AR_OUT","epc_extract").replace(/\\/g,"/");
var WANT_IMAGES=env("AR_IMAGES","1")!="0", WANT_SA=env("AR_SA","1")!="0", RESUME=env("AR_RESUME","1")!="0";
var SLEEP=+env("AR_SLEEP_MS","0"), LIMIT=+env("AR_LIMIT","0");
var IMG_DIR=OUT+"/images/epc";
new File(IMG_DIR).mkdirs();

var cl=new URLClassLoader([new File(env("AR_TBJDBC","tbjdbc.jar")).toURI().toURL()], ClassLoader.getSystemClassLoader());
var drv=Clazz.forName("transbase.jdbc.Driver",true,cl).newInstance();
function openDb(db,user,pw){var p=new Properties();p.setProperty("user",user);p.setProperty("password",pw);p.setProperty("characterEncoding","UTF-8");
  var c=drv.connect("jdbc:transbase://"+HOST+":"+PORT+"/"+db,p);if(c==null)throw "null connection "+db;return c;}
function rot13(s){ return s.replace(/[A-Za-z]/g, function(c){ var b=c<='Z'?65:97; return String.fromCharCode((c.charCodeAt(0)-b+13)%26+b); }); }
function trim(s){ return s==null?"":(""+s).replace(/^\s+|\s+$/g,""); }
function q(s){ return "'"+(""+s).replace(/'/g,"''")+"'"; }
function padSa(s){ s=trim(s); return s.length==5?" "+s:s; }   // SANUM is CHAR(6); the server pads 5-digit ones

// connect: read + ROT13 the catalogue password from SPECIALFILES
var sf=openDb("SPECIALFILES","tbuser","");
var r0=sf.createStatement().executeQuery("select pword from PWORD_V"); r0.next(); var PW=rot13(r0.getString(1)); r0.close(); sf.close();
var dbs={};
function db(name){ if(!(name in dbs)){ try{ dbs[name]=openDb(name,"tbuser",PW); }catch(e){ dbs[name]=null; print("  (database "+name+" not available)"); } } return dbs[name]; }
if(!db("ALLTEXT")) throw "cannot open ALLTEXT";

// rows(sql) -> array of {COLUMN: trimmed string}; the column names are upper case
function rows(sql,dbname){
  var st=db(dbname||"ALLTEXT").createStatement(), rs=st.executeQuery(sql), md=rs.getMetaData(), n=md.getColumnCount(), out=[];
  var names=[]; for(var i=1;i<=n;i++) names.push((""+md.getColumnLabel(i)).toUpperCase());
  while(rs.next()){ var o={}; for(var j=1;j<=n;j++) o[names[j-1]]=trim(rs.getString(j)); out.push(o); }
  rs.close(); st.close(); return out;
}

// ---------------------------------------------------------------- part numbers
// PARTTYPE + PARTNUM; "A" + 10 digits is written 3-3-2-2 (A 115 223 14 12), any
// extra digits (colour code) after; "N" standard parts 6-6 (N 000931 012182).
function partNo(pt,pn){
  pt=trim(pt); pn=trim(pn).replace(/\s+/g,"");
  var compact=pt+pn, spaced;
  if(pt=="A" && /^\d{10,}$/.test(pn)) spaced="A "+pn.substr(0,3)+" "+pn.substr(3,3)+" "+pn.substr(6,2)+" "+pn.substr(8,2)+(pn.length>10?" "+pn.substr(10):"");
  else if(pt=="N" && /^\d{12}$/.test(pn)) spaced="N "+pn.substr(0,6)+" "+pn.substr(6);
  else spaced=(pt+" "+pn).replace(/^\s+/,"");
  // the bare digits too: FTS reads "A1152231412" as one token, and shops type either
  return spaced==compact ? compact : spaced+" ("+compact+(pt && /^\d+$/.test(pn) ? ", "+pn : "")+")";
}
function isPlaceholder(pt,pn){ var s=pt+pn; return s.indexOf("XXXXX")>=0 || s.indexOf("YYYYY")>=0; }

// --------------------------------------------------------- fits / quantities
// BM parts: SUBMODS is the list of baumuster suffixes (3 chars each) the row
// fits; QUANTBM is the quantity per slot ("001" = 1, "-" = not stated, which
// is how a superseded number is listed). fits() turns them into
// "qty 1: 115.000, 115.002; qty 2: 115.114" or "qty 2, all models".
function slots3(s){ var out=[]; for(var i=0;i<s.length;i+=3) out.push(trim(s.substr(i,3))); return out; }
function qtyLabel(qv){ qv=trim(qv); return /^\d+$/.test(qv) ? ""+(+qv) : ""; }
function fits(pairs, allBm){            // pairs: [[baumuster, qty-label], ...]
  if(!pairs.length) return "";
  var byQ={}, order=[], seen={};
  pairs.forEach(function(p){ if(!(p[1] in byQ)){ byQ[p[1]]=[]; order.push(p[1]); } if(!seen[p[0]+"|"+p[1]]){ seen[p[0]+"|"+p[1]]=1; byQ[p[1]].push(p[0]); } });
  var covered={}; pairs.forEach(function(p){ covered[p[0]]=1; });
  var all = allBm.length>0 && allBm.every(function(b){ return covered[b]; });
  if(all && order.length==1) return order[0] ? "qty "+order[0]+", all models" : "all models";
  return order.map(function(k){ return (k?"qty "+k+": ":"")+byQ[k].join(", "); }).join("; ");
}

// ------------------------------------------------------------- FV4 drawings
load(__DIR__+"epc_fv4.js");                     // decodeFV4(): the EPC client's own drawing format
load(__DIR__+"epc_footnotes.js");               // fnDecode() / fnRefs(): part footnotes

// image name -> relative PNG path under images/ (or "" if not found / not decodable)
var imgStats={written:0, cached:0, missing:0, undecodable:0, partial:0, other:0}, imgMemo={}, missingLog=[];
function blob(dbname,name){
  var d=db(dbname); if(!d) return null;
  var st=d.createStatement(), rs=st.executeQuery("select IMAGE from IMAGE_V where IMAGE_NAME = "+q(name)), b=null;
  if(rs.next()){ var in_=rs.getBinaryStream(1);
    if(in_!=null){ var bo=new ByteArrayOutputStream(), chunk=new ByteArr(65536), k; while((k=in_.read(chunk))>0) bo.write(chunk,0,k); in_.close(); b=bo.toByteArray(); }
    else b=rs.getBytes(1); }
  rs.close(); st.close(); return b;
}
function magic(b){
  function at(i){ return b.length>i ? b[i]&255 : -1; }
  if(at(0)==0x47&&at(1)==0x49&&at(2)==0x46) return "gif";
  if(at(0)==0x89&&at(1)==0x50&&at(2)==0x4E) return "png";
  if(at(0)==0xFF&&at(1)==0xD8) return "jpg";
  if((at(0)==0x49&&at(1)==0x49)||(at(0)==0x4D&&at(1)==0x4D)) return "tif";
  return "fv4";
}
function fileSafe(name){ return name.replace(/[^A-Za-z0-9._-]/g,"_"); }
function image(candidates){            // candidates: [[db, name], ...] tried in order
  var key=candidates.map(function(c){return c.join(":");}).join("|");
  if(key in imgMemo) return imgMemo[key];
  var rel="", found=false;
  for(var i=0;i<candidates.length && !rel;i++){
    var dbname=candidates[i][0], name=candidates[i][1], base=fileSafe(name);
    var exts=["png","gif","jpg"];
    for(var e=0;e<exts.length;e++) if(new File(IMG_DIR+"/"+base+"."+exts[e]).isFile()){ rel="epc/"+base+"."+exts[e]; imgStats.cached++; found=true; break; }   // on disk from an earlier run: found, not missing
    if(rel) break;
    var b=blob(dbname,name); if(b==null) continue;
    found=true; var kind=magic(b);
    if(kind=="gif"||kind=="png"||kind=="jpg"){ var fo=new FileOutputStream(IMG_DIR+"/"+base+"."+kind); fo.write(b); fo.close(); rel="epc/"+base+"."+kind; imgStats.other++; break; }
    if(kind!="fv4"){ imgStats.undecodable++; missingLog.push(name+" ("+kind+")"); break; }
    var d=decodeFV4(b);
    if(!d || d.rows==0){ imgStats.undecodable++; missingLog.push(name+" (fv4 header/data, "+b.length+" bytes)"); break; }
    if(d.rows<d.hdrRows){ imgStats.partial++; if(imgStats.partial<=5) print("  partial drawing "+name+": "+d.rows+"/"+d.hdrRows+" rows, "+b.length+" bytes"); }
    ImageIO.write(d.img.getSubimage(0,0,1024,Math.max(d.contentRows,1)),"png",new File(IMG_DIR+"/"+base+".png"));
    rel="epc/"+base+".png"; imgStats.written++;
  }
  if(!found){ imgStats.missing++; missingLog.push(candidates.map(function(c){return c.join(":");}).join(" | ")+" (not found)"); }
  imgMemo[key]=rel; return rel;
}
// BM drawing names (decompiled OrphanNotesBL): "B" + group(2) + subgroup(3) +
// SEQNO(2) + RESTIMG, e.g. B24001000003.940127, in BM_IMAGES_ARC when ARCHIVED=1;
// BM_IMAGES holds catnum + group + subgroup + SEQNO names (8272400101).
function bmImage(cat,r){
  if((r.SEQNO+r.RESTIMG).indexOf("99999.")>=0) return "";   // the client's "no drawing" placeholder (EPCNavItemPanel)
  var bname=r.IMGTYPE+r.GROUPNUM+r.SUBGRP+r.SEQNO+r.RESTIMG, cname=cat+r.GROUPNUM+r.SUBGRP+r.SEQNO;
  var c = r.ARCHIVED=="1" ? [["BM_IMAGES_ARC",bname],["BM_IMAGES",bname],["BM_IMAGES",cname]]
                          : [["BM_IMAGES",bname],["BM_IMAGES",cname],["BM_IMAGES_ARC",bname]];
  return image(c);
}
// SA drawing names: "S" + SANUM(6) + SEQNO(2) + RESTIMG (S02016600001.120807)
function saImage(sanum,r){
  if((r.SEQNO+r.RESTIMG).indexOf("99999.")>=0) return "";
  var s6=trim(sanum), names=[];
  [("000000"+s6).slice(-6), (" "+s6).slice(-6)].forEach(function(n6){ var nm="S"+n6+r.SEQNO+r.RESTIMG; if(names.indexOf(nm)<0) names.push(nm); });
  var c=[]; names.forEach(function(nm){ if(r.ARCHIVED=="1"){ c.push(["SA_IMAGES_ARC",nm]); c.push(["SA_IMAGES",nm]); } else { c.push(["SA_IMAGES",nm]); c.push(["SA_IMAGES_ARC",nm]); } });
  return image(c);
}

// ------------------------------------------------------------ dictionaries
var nouns={}, nc=0;
rows("select NOUNIDX, NOUN from part_nouns_v where LANG="+q(LANG)).forEach(function(r){ nouns[r.NOUNIDX]=r.NOUN; nc++; });
print("ALLTEXT open. catalogs="+CATNUM+" lang="+LANG+" out="+OUT+"  ("+nc+" part names)");
function descSql(alias){ return "coalesce((select description from part_descs_v where descidx="+alias+".descidx and lang="+q(LANG)+"),"
  +" (select description from part_descs_v where descidx="+alias+".descidx and lang='E')) as DESCRIPTION"; }

// footnote rows, TEXT untrimmed at the front (revision-9 fields are positional)
function fnRows(sql){
  var st=db("ALLTEXT").createStatement(), rs=st.executeQuery(sql), out=[];
  while(rs.next()) out.push({FTNTNUM:trim(rs.getString("FTNTNUM")),
    ABBR:trim(rs.getString("ABBR")), CONTINUED:trim(rs.getString("CONTINUED")), TEXT:rs.getString("TEXT")});
  rs.close(); st.close(); return out;
}
function bmFootnotes(cat,g){
  return fnDecode(fnRows("select a.FTNTNUM, b.ABBR, b.CONTINUED, b.TEXT from BM_FOOTNOTES_V a, BM_FOOTNOTES_DICTIONARY_V b "
    +"where a.CATNUM="+q(cat)+" and a.GROUPNUM="+q(g)+" and a.DESCIDX=b.DESCIDX and (b.LANG="+q(LANG)+" or b.LANG='N') "
    +"order by a.FTNTNUM, b.SEQNUM, b.CONTINUED"));
}
function saFootnotes(p6){
  return fnDecode(fnRows("select a.FTNTNUM, b.ABBR, b.CONTINUED, b.TEXT from SA_FOOTNOTES_V a, SA_FOOTNOTES_DICTIONARY_V b "
    +"where a.SANUM="+q(p6)+" and a.DESCIDX=b.DESCIDX and (b.LANG="+q(LANG)+" or b.LANG='N') order by a.FTNTNUM, b.SEQNUM, b.CONTINUED"));
}
// a part's footnote numbers -> its notes entry; collects the texts the page needs
function fnNote(codes, fn, used){
  var refs=fnRefs(codes); if(!refs.length) return "";
  refs.forEach(function(c){ if(fn[c]) used[c]=fn[c]; });
  return "fn "+refs.join(", ");
}
function fnSection(used){
  var ks=Object.keys(used).sort(); if(!ks.length) return [];
  return ["", "Footnotes:"].concat(ks.map(function(k){ return "fn "+k+": "+used[k]; }));
}
var fnCount=0;

// ------------------------------------------------------------ catalog list
// Gearbox (GA) and other aggregate catalogs are left out: there is no
// car -> gearbox map, and "MODEL 722" would make a gearbox look like a car.
var catalogs=[];
function byAgg(agg){ return rows("select unique CATNUM from models_v where class='1' and aggtype="+q(agg)+" and catnum<>'598' order by CATNUM").map(function(r){return r.CATNUM;}); }
CATNUM.split(",").map(trim).filter(function(s){return s;}).forEach(function(tok){
  var add = tok.toLowerCase()=="all" ? byAgg("FG") : tok.toLowerCase()=="engines" ? byAgg("M") : [tok];
  add.forEach(function(c){ if(catalogs.indexOf(c)<0) catalogs.push(c); });
});
var started=0;
print("catalogs to pull: "+catalogs.length);

// ------------------------------------------------------------ output + resume
var REC=OUT+"/records.jsonl", DONE=OUT+"/done_catalogs.txt";
// The record format. A resumed run only appends to a corpus of the same
// format; after a change (2: footnotes) the records are rebuilt from scratch
// -- the old file is kept as a .bak -- and the drawings on disk are reused.
var FORMAT=2, FORMAT_LINE="#format "+FORMAT;
var done={}, dokId=0;
if(RESUME && new File(DONE).isFile() && new File(REC).isFile()
   && Files.readAllLines(Paths.get(DONE),StandardCharsets.UTF_8).indexOf(FORMAT_LINE)<0){
  var bak=OUT+"/records.before-format"+FORMAT+"."+(new Date()).toISOString().replace(/[^0-9]/g,"").substr(0,14)+".jsonl.bak";
  Files.move(Paths.get(REC),Paths.get(bak));
  print("corpus is an older format: kept as "+bak+"; rebuilding the records (drawings on disk are reused)");
  RESUME=false;
}
if(RESUME && new File(DONE).isFile() && new File(REC).isFile()){
  Files.readAllLines(Paths.get(DONE),StandardCharsets.UTF_8).forEach(function(l){ l=trim(l); if(l) done[l]=1; });
  // a crash mid-SA-phase leaves no SA lines (they are appended in one go), so the file is clean
  Files.readAllLines(Paths.get(REC),StandardCharsets.UTF_8).forEach(function(l){ var m=/"dok_id":(\d+)/.exec(l); if(m && +m[1]>dokId) dokId=+m[1]; });
  print("resume: "+Object.keys(done).length+" catalogs already done, last dok_id "+dokId);
} else { writer(REC,false).close(); var dw=writer(DONE,false); dw.write(FORMAT_LINE+"\n"); dw.close(); }
// always UTF-8: FileWriter would use the Windows code page (cp1252) and the
// importer reads UTF-8 (part texts carry Ü, Ø, ° ...)
function writer(path,append){ return new BufferedWriter(new OutputStreamWriter(new FileOutputStream(path,append),StandardCharsets.UTF_8)); }
function appendLines(path,lines){ var w=writer(path,true); lines.forEach(function(l){ w.write(l); w.write("\n"); }); w.close(); }

var partRows=0, recCount=0, emptyRecs=0, saIndex={}, pulledSeries={}, t0=Date.now();
function pause(){ if(SLEEP>0) Thread.sleep(SLEEP); }

// ------------------------------------------------------------ BM catalogs
catalogs.forEach(function(cat, ci){
  // models: series, baumuster, sales names (catalog order)
  var models=rows("select TYPE, SUBBM1, SALESDES, AGGTYPE from models_v where catnum="+q(cat));
  // an engine catalog (aggtype M): TYPE is the engine series, filed as "ENGINE 112"
  var isEngine=models.length>0 && models.every(function(m){ return m.AGGTYPE=="M"; });
  var ROLE=isEngine?"ENGINE ":"MODEL ", noun=isEngine?"engine ":"model ";
  var seriesSet={}, bm=[], sales=[];
  models.forEach(function(m){ if(!m.TYPE) return; seriesSet[m.TYPE]=1; var b=m.TYPE+(m.SUBBM1?"."+m.SUBBM1:"");
    if(bm.indexOf(b)<0) bm.push(b); var sd=b+(m.SALESDES?" "+m.SALESDES:""); if(sales.indexOf(sd)<0) sales.push(sd); });
  var series=Object.keys(seriesSet); if(!isEngine) series.forEach(function(s){ pulledSeries[s]=1; });
  // SA options this catalog offers (kept even for resumed catalogs: the SA phase
  // needs them). Not for engine catalogs: SA strokes name car series.
  if(WANT_SA && !isEngine) rows("select SANUM, GROUPNUM, CODEONE, CODETWO from BM_SAIDX_V where CATNUM="+q(cat)).forEach(function(r){
    var k=trim(r.SANUM), e=saIndex[k]||(saIndex[k]={groups:{}, codes:{}});
    e.groups[r.GROUPNUM]=1; slots3(r.CODEONE+r.CODETWO).forEach(function(c){ if(c) e.codes[c]=1; }); });
  if(done[cat]){ return; }
  if(LIMIT && started>=LIMIT) return;
  started++;
  if(!series.length){ print("catalog "+cat+": no models_v rows, skipped"); appendLines(DONE,[cat]); return; }
  // SUBMODS are SUBBM1 suffixes; map each back to its series (a catalog can hold several)
  var subSeries={}; models.forEach(function(m){ if(m.TYPE && m.SUBBM1 && !subSeries[m.SUBBM1]) subSeries[m.SUBBM1]=m.TYPE; });
  var bmOf=function(sub){ return (subSeries[sub]||series[0])+"."+sub; };

  var groups=rows("select GROUPNUM, TEXT from bm_group_v join BM_DICTIONARY_V using (DESCIDX) where LANG="+q(LANG)+" and CATNUM="+q(cat)+" order by GROUPNUM");
  var recs=[], catParts=0;
  groups.forEach(function(g){
    // subgroups: the named list, plus any subgroup that only shows up in the parts table
    var subs=[], seen={};
    rows("select SUBGRP, TEXT from BM_SUBGRP_V where CATNUM="+q(cat)+" and GROUPNUM="+q(g.GROUPNUM)+" and LANG="+q(LANG)+" order by SUBGRP")
      .forEach(function(r){ if(!seen[r.SUBGRP]){ seen[r.SUBGRP]=1; subs.push({sg:r.SUBGRP, text:r.TEXT}); } });
    rows("select unique SUBGRP from BM_PARTS2_V where CATNUM="+q(cat)+" and GROUPNUM="+q(g.GROUPNUM))
      .forEach(function(r){ if(!seen[r.SUBGRP]){ seen[r.SUBGRP]=1; subs.push({sg:r.SUBGRP, text:""}); } });
    var fn=bmFootnotes(cat,g.GROUPNUM);
    // drawings, by subgroup
    var npg={};
    if(WANT_IMAGES) rows("select IMGTYPE, SEQNO, RESTIMG, SUBGRP, ARCHIVED, GROUPNUM from BM_NPG_V where CATNUM="+q(cat)+" and GROUPNUM="+q(g.GROUPNUM)+" order by SEQNUM")
      .forEach(function(r){ (npg[r.SUBGRP]=npg[r.SUBGRP]||[]).push(r); });

    subs.forEach(function(sg){
      var lines=[], n=0, used={};
      rows("select CALLOUT, PARTTYPE, PARTNUM, QUANTBM, SUBMODS, NOUNIDX, PARTREM, REPTYPE, REPPNO, FOOTNOTES, "+descSql("a")
        +" from BM_PARTS2_V a where CATNUM="+q(cat)+" and GROUPNUM="+q(g.GROUPNUM)+" and SUBGRP="+q(sg.sg)+" order by SEQNUM")
      .forEach(function(r){
        if(isPlaceholder(r.PARTTYPE,r.PARTNUM)) return;
        var sm=slots3(r.SUBMODS), qs=slots3(r.QUANTBM), pairs=[];
        for(var i=0;i<sm.length;i++) if(sm[i]) pairs.push([bmOf(sm[i]), qtyLabel(qs[i]||"")]);
        var name=nouns[r.NOUNIDX]||"", desc=r.DESCRIPTION||"";
        var label=name && desc && desc!=name ? name+", "+desc : (name||desc);
        var notes=[]; if(r.PARTREM) notes.push(r.PARTREM);
        if(r.REPPNO) notes.push("replaced by "+partNo(r.REPTYPE,r.REPPNO));
        var f=fnNote(r.FOOTNOTES,fn,used); if(f) notes.push(f);
        lines.push((r.CALLOUT||"-")+" | "+partNo(r.PARTTYPE,r.PARTNUM)+" | "+label+" | "+(fits(pairs,bm)||"-")+" | "+notes.join("; "));
        n++;
      });
      partRows+=n; catParts+=n;
      var imgs=[];
      (npg[sg.sg]||[]).forEach(function(r){ var f=bmImage(cat,r); if(f && imgs.indexOf(f)<0) imgs.push(f); });
      var title="Parts: "+series.map(function(s){return noun+s;}).join(", ")+" (EPC catalog "+cat+"), group "+g.GROUPNUM+(g.TEXT?" "+g.TEXT:"")
        +" / subgroup "+sg.sg+(sg.text?" "+sg.text:"");
      var body=[title, (isEngine?"Engines":"Models")+" in this catalog: "+sales.join(", "), ""];
      if(n){ body.push("Callout | Part number | Name | Fits | Notes"); body=body.concat(lines).concat(fnSection(used)); fnCount+=Object.keys(used).length; }
      else { body.push("(no parts listed)"); emptyRecs++; }
      recs.push({
        dok_id: ++dokId, lang: "en", title: title, body_text: body.join("\n"), body_format: "sids",
        model_series: isEngine?[]:series, baumuster: isEngine?[]:bm, engine_series: isEngine?series:[],
        applicability_notes: series.map(function(s){return ROLE+s;}).concat(bm.map(function(b){return ROLE+b;})),
        cross_refs: [], image_refs: [], figures: [], image_files: imgs,
        source_type: "mercedes-epc", catnum: cat, group: g.GROUPNUM, subgroup: sg.sg
      });
      pause();
    });
  });
  appendLines(REC, recs.map(function(r){ return JSON.stringify(r); }));
  appendLines(DONE,[cat]);
  recCount+=recs.length;
  print("catalog "+cat+" ("+(ci+1)+"/"+catalogs.length+", series "+series.join(",")+"): "+recs.length+" records, "+catParts+" parts; images "+JSON.stringify(imgStats)+"; "+Math.round((Date.now()-t0)/1000)+" s");
});

// ------------------------------------------------------------ SA (option) parts
// One record per (SA, subgroup), written once however many catalogs offer the
// SA. A part row lists strokes ("01,02,") with a quantity per stroke; a stroke
// maps to baumuster per model series via sa_strokes_v. Only the series pulled
// in this run are kept, so a one-catalog run does not file stray models.
var saRecs=[], saParts=0;
var SA_DONE="#sa-done";                         // in done_catalogs.txt once the SA records are appended
if(WANT_SA && done[SA_DONE]) print("SA options: already written (resume)");
else if(WANT_SA){
  var sas=Object.keys(saIndex).sort();
  print("SA options: "+sas.length);
  sas.forEach(function(sa, si){
    var p6=padSa(sa), info=saIndex[sa];
    var strokes={};                              // STRKVER -> [baumuster]
    rows("select MODEL, STRKVER, SUBMOD from sa_strokes_v where SANUM="+q(p6)).forEach(function(r){
      var model=trim(r.MODEL); if(!pulledSeries[model]) return;
      var l=strokes[r.STRKVER]||(strokes[r.STRKVER]=[]);
      slots3(r.SUBMOD).forEach(function(s){ if(s){ var b=model+"."+s; if(l.indexOf(b)<0) l.push(b); } });
    });
    if(!Object.keys(strokes).length) return;
    var saText=rows("select TEXT from SA_DESCS_V join SA_DICTIONARY_V using (DESCIDX) where LANG="+q(LANG)+" and SANUM="+q(p6)+" order by SEQNUM")
      .map(function(r){return r.TEXT;}).join(" ");
    var subs=rows("select SUBGRP, TEXT from sa_subgrp_V where SANUM="+q(p6)+" and LANG="+q(LANG)+" order by SUBGRP");
    if(!subs.length) subs=rows("select unique SUBGRP from SA_PARTS_V where SANUM="+q(p6)).map(function(r){ return {SUBGRP:r.SUBGRP, TEXT:""}; });
    var npg={};
    if(WANT_IMAGES) rows("select IMGTYPE, SEQNO, RESTIMG, SUBGRP, ARCHIVED from SA_NPG_V where SANO="+q(p6)+" order by SEQNUM")
      .forEach(function(r){ (npg[r.SUBGRP]=npg[r.SUBGRP]||[]).push(r); });
    var codes=Object.keys(info.codes), groupsTxt=Object.keys(info.groups).sort().join(", ");
    var fn=saFootnotes(p6);
    subs.forEach(function(sg){
      var lines=[], seriesSeen={}, bmSeen={}, used={};
      rows("select ITEMNO, PARTTYP, PARTNUM, QUANTSA, STROKE, NOUNIDX, PARTREM, REPTYPE, REPPNO, FOOTNOT, "+descSql("a")
        +" from SA_PARTS_V a where SANUM="+q(p6)+" and SUBGRP="+q(sg.SUBGRP)+" order by SEQNO")
      .forEach(function(r){
        if(isPlaceholder(r.PARTTYP,r.PARTNUM)) return;
        var ss=r.STROKE.split(","), qq=r.QUANTSA.split(","), pairs=[];
        for(var i=0;i<ss.length;i++){ var sv=trim(ss[i]); if(!sv || !strokes[sv]) continue;
          strokes[sv].forEach(function(b){ pairs.push([b, qtyLabel(qq[i]||"")]); seriesSeen[b.split(".")[0]]=1; bmSeen[b]=1; }); }
        if(!pairs.length) return;                // only fits series not pulled in this run
        var name=nouns[r.NOUNIDX]||"", desc=r.DESCRIPTION||"";
        var label=name && desc && desc!=name ? name+", "+desc : (name||desc);
        var notes=[]; if(r.PARTREM) notes.push(r.PARTREM);
        if(r.REPPNO) notes.push("replaced by "+partNo(r.REPTYPE,r.REPPNO));
        var f=fnNote(r.FOOTNOT,fn,used); if(f) notes.push(f);
        lines.push((r.ITEMNO||"-")+" | "+partNo(r.PARTTYP,r.PARTNUM)+" | "+label+" | "+fits(pairs,[])+" | "+notes.join("; "));
      });
      if(!lines.length) return;
      saParts+=lines.length;
      var imgs=[];
      (npg[sg.SUBGRP]||[]).forEach(function(r){ var f=saImage(sa,r); if(f && imgs.indexOf(f)<0) imgs.push(f); });
      var series=Object.keys(seriesSeen).sort(), bms=Object.keys(bmSeen).sort();
      var title="Parts: option SA "+sa+(codes.length?" (code "+codes.join(", ")+")":"")+", "+series.map(function(s){return "model "+s;}).join(", ")
        +" / subgroup "+sg.SUBGRP+(sg.TEXT?" "+sg.TEXT:"");
      var body=[title, "Option: "+(saText||"-"), "Catalog groups: "+groupsTxt, "",
                "Item | Part number | Name | Fits | Notes"].concat(lines).concat(fnSection(used));
      fnCount+=Object.keys(used).length;
      saRecs.push(JSON.stringify({
        dok_id: ++dokId, lang: "en", title: title, body_text: body.join("\n"), body_format: "sids",
        model_series: series, baumuster: bms,
        applicability_notes: series.map(function(s){return "MODEL "+s;}).concat(bms.map(function(b){return "MODEL "+b;})),
        cross_refs: [], image_refs: [], figures: [], image_files: imgs,
        source_type: "mercedes-epc", sanum: sa, subgroup: sg.SUBGRP
      }));
    });
    if((si+1)%200==0) print("  SA "+(si+1)+"/"+sas.length+": "+saRecs.length+" records so far");
  });
  appendLines(REC, saRecs);                      // in one go, so a crash above leaves records.jsonl clean
  appendLines(DONE,[SA_DONE]);                  // a resumed run must not append them again
}

// ------------------------------------------------------------ manifest
var total=0; Files.readAllLines(Paths.get(REC),StandardCharsets.UTF_8).forEach(function(l){ if(trim(l)) total++; });
// page_no_prefix / doc_type: EPC is searched inside WIS cars' scopes (index.link_sources),
// so its page numbers must never equal a WIS DokID
var manifest={schema_version:"1.3", source_type:"mercedes-epc", source_id:HOST+"/ALLTEXT", page_no_prefix:"P", doc_type:"parts", corpus_format:FORMAT,
  database:"ALLTEXT", language:{iso:"en"}, catalogs:catalogs, record_count:total,
  this_run:{bm_records:recCount, bm_part_rows:partRows, sa_records:saRecs.length, sa_part_rows:saParts, footnotes_on_pages:fnCount, empty_records:emptyRecs, images:imgStats},
  body_format_counts:{sids:total}, records_file:"records.jsonl", extracted_at:(new Date()).toISOString()};
var mw=writer(OUT+"/manifest.json",false); mw.write(JSON.stringify(manifest,null,2)); mw.close();
if(missingLog.length){ appendLines(OUT+"/images_missing.txt", missingLog.slice(0,5000)); }
for(var k in dbs) if(dbs[k]) dbs[k].close();
print("\nDONE: "+total+" records in "+REC+" (this run: "+recCount+" catalog records / "+partRows+" parts, "
  +saRecs.length+" SA records / "+saParts+" parts); images "+JSON.stringify(imgStats)+"; "+Math.round((Date.now()-t0)/1000)+" s");
