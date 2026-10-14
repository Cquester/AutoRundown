// AutoRundown EPC parts probe (probe_epc.js only lists tables): one read-only
// pass that answers the open schema
// questions before pull_epc.js grows (docs/notes/epc-source.md, "Remaining
// build items"). Prints counts, column lists and a few sample rows; writes
// nothing except one sample image to AR_OUT (if set). Same login as
// pull_epc.js; the password is never printed.
//
//   jjs.exe probe_epc_parts.js
// Env: AR_HOST, AR_PORT, AR_LANG, AR_CATNUM (001), AR_TBJDBC, AR_OUT (optional),
//      AR_STEPS (e.g. "7,8,9"; default all)

var System=Java.type("java.lang.System"), File=Java.type("java.io.File");
var URLClassLoader=Java.type("java.net.URLClassLoader"), Properties=Java.type("java.util.Properties");
var Clazz=Java.type("java.lang.Class"), ClassLoader=Java.type("java.lang.ClassLoader");
var FileOutputStream=Java.type("java.io.FileOutputStream");
function env(k,d){var v=System.getenv(k);return(v==null||v=="")?d:v;}
var HOST=env("AR_HOST","192.168.56.101"), PORT=env("AR_PORT","2034"), LANG=env("AR_LANG","E");
var CATNUM=env("AR_CATNUM","001"), OUT=env("AR_OUT",""), STEPS=env("AR_STEPS","");
var cl=new URLClassLoader([new File(env("AR_TBJDBC","tbjdbc.jar")).toURI().toURL()], ClassLoader.getSystemClassLoader());
var drv=Clazz.forName("transbase.jdbc.Driver",true,cl).newInstance();
function openDb(db,user,pw){var p=new Properties();p.setProperty("user",user);p.setProperty("password",pw);
  var c=drv.connect("jdbc:transbase://"+HOST+":"+PORT+"/"+db,p);if(c==null)throw "null connection "+db;return c;}
function rot13(s){return s.replace(/[A-Za-z]/g,function(c){var b=c<='Z'?65:97;return String.fromCharCode((c.charCodeAt(0)-b+13)%26+b);});}
function trim(s){return s==null?"":(""+s).replace(/^\s+|\s+$/g,"");}
function q(s){return "'"+(""+s).replace(/'/g,"''")+"'";}

var sf=openDb("SPECIALFILES","tbuser","");
var r0=sf.createStatement().executeQuery("select pword from PWORD_V"); r0.next(); var PW=rot13(r0.getString(1)); r0.close(); sf.close();
var dbs={};
function db(name){ if(!dbs[name]) dbs[name]=openDb(name,"tbuser",PW); return dbs[name]; }

// run sql, return array of row objects (column name -> trimmed string); max rows optional
function rows(sql,max,dbname){
  var st=db(dbname||"ALLTEXT").createStatement(); if(max) st.setMaxRows(max);
  var rs=st.executeQuery(sql), md=rs.getMetaData(), n=md.getColumnCount(), out=[];
  while(rs.next()){ var o={}; for(var i=1;i<=n;i++){ var t=md.getColumnTypeName(i);
      o[md.getColumnName(i)] = /BLOB|BITS|BINARY/i.test(t) ? "<"+t+">" : trim(rs.getString(i)); } out.push(o); }
  rs.close(); st.close(); return out;
}
function one(sql,dbname){ var r=rows(sql,1,dbname); if(!r.length) return ""; for(var k in r[0]) return r[0][k]; }
function step(title,fn){ print("\n== "+title); try{ fn(); }catch(e){ print("  ERROR: "+e); } }
function show(rs){ for(var i=0;i<rs.length;i++) print("  "+JSON.stringify(rs[i])); }

print("probe catnum="+CATNUM+" lang="+LANG);

// 1. subgroup completeness
step("1. subgroups of catalog "+CATNUM, function(){
  var all=rows("select GROUPNUM, SUBGRP, PARTTYPE, PARTNUM from BM_PARTS2_V where CATNUM="+q(CATNUM));
  var sgAll={}, sgReal={}, real=0;
  all.forEach(function(r){ var k=r.GROUPNUM+"/"+r.SUBGRP; sgAll[k]=(sgAll[k]||0)+1;
    var pn=r.PARTTYPE+r.PARTNUM; if(pn.indexOf("XXXXX")<0 && pn.indexOf("YYYYY")<0){ sgReal[k]=(sgReal[k]||0)+1; real++; } });
  print("  BM_PARTS2_V rows "+all.length+", real (non-placeholder) "+real);
  print("  distinct (group,subgroup) in BM_PARTS2_V: "+Object.keys(sgAll).length+", with real parts: "+Object.keys(sgReal).length);
  var sgv={}, sgvE={};
  rows("select GROUPNUM, SUBGRP, LANG from BM_SUBGRP_V where CATNUM="+q(CATNUM)).forEach(function(r){
    var k=r.GROUPNUM+"/"+r.SUBGRP; sgv[k]=1; if(r.LANG==LANG) sgvE[k]=1; });
  print("  BM_SUBGRP_V: "+Object.keys(sgv).length+" any lang, "+Object.keys(sgvE).length+" lang "+LANG);
  var npg={}; rows("select unique GROUPNUM, SUBGRP from BM_NPG_V where CATNUM="+q(CATNUM)).forEach(function(r){ npg[r.GROUPNUM+"/"+r.SUBGRP]=1; });
  print("  BM_NPG_V distinct (group,subgroup): "+Object.keys(npg).length);
  var missing=Object.keys(sgReal).filter(function(k){return !sgvE[k];});
  print("  real-part subgroups missing from BM_SUBGRP_V["+LANG+"]: "+missing.length+(missing.length?"  e.g. "+missing.slice(0,15).join(" "):""));
  var lost=0; missing.forEach(function(k){ lost+=sgReal[k]; }); print("  real part rows in those: "+lost);
});

// 2. column lists of the views we need
step("2. columns", function(){
  ["BM_PARTS2_V","BM_NPG_V","MODELS_V","BM_SAIDX_V","SA_PARTS_V","SA_NPG_V","SA_SUBGRP_V","APPL_CAT_BASE_V","IMAGE_V"].forEach(function(v){
    try{ var st=db("ALLTEXT").createStatement(); st.setMaxRows(1); var rs=st.executeQuery("select * from "+v), md=rs.getMetaData(), cols=[];
      for(var i=1;i<=md.getColumnCount();i++) cols.push(md.getColumnName(i)+":"+md.getColumnTypeName(i)); rs.close(); st.close();
      print("  "+v+" ("+cols.length+"): "+cols.join(", "));
    }catch(e){ print("  "+v+": ERROR "+e); }
  });
});

// 3. models of the catalog + QUANTBM / SUBMODS shape
step("3. models + QUANTBM", function(){
  var m=rows("select type, subbm1, subbm2, salesdes, class, aggtype, appinf, remarks from models_v where catnum="+q(CATNUM));
  print("  models_v rows: "+m.length+" (server order)"); show(m);
  var qs=rows("select unique QUANTBM, SUBMODS from BM_PARTS2_V where CATNUM="+q(CATNUM),25);
  print("  sample QUANTBM | SUBMODS (|QUANTBM| = len):");
  qs.forEach(function(r){ print("  ["+r.QUANTBM+"] len "+r.QUANTBM.length+" | ["+r.SUBMODS+"]"); });
  show(rows("select GROUPNUM, SUBGRP, CALLOUT, PARTTYPE, PARTNUM, QUANTBM, SUBMODS, CODEB, STEERING, INDENT, FOOTNOTES, PARTREM from BM_PARTS2_V where CATNUM="+q(CATNUM)+" and PARTNUM not like '%XXXXX%' order by GROUPNUM, SUBGRP, SEQNUM",8));
});

// 4. images: NPG rows, the real IMAGE_NAME shape, one blob
step("4. images", function(){
  print("  BM_NPG_V rows for catalog: "+one("select count(*) from BM_NPG_V where CATNUM="+q(CATNUM)));
  var npg=rows("select IMGTYPE, SEQNO, RESTIMG, CONTREC, CALLOUT, SUBGRP, ARCHIVED, GROUPNUM, SEQNUM from BM_NPG_V where CATNUM="+q(CATNUM)+" order by GROUPNUM, SEQNUM",6);
  show(npg); if(!npg.length) return;
  var n=npg[0], g=n.GROUPNUM, guess=n.IMGTYPE+g+n.SUBGRP+n.SEQNO+n.RESTIMG;
  print("  guessed name from row 1: ["+guess+"]");
  ["ALLTEXT","COMM","BM_IMAGES"].forEach(function(d){
    try{ var names=rows("select IMAGE_NAME from IMAGE_V where IMAGE_NAME like "+q("%"+g+n.SUBGRP+"%"),8,d);
      print("  IMAGE_V in "+d+": "+names.length+" names like *"+g+n.SUBGRP+"*: "+names.map(function(x){return "["+x.IMAGE_NAME+"]";}).join(" "));
      print("  IMAGE_V in "+d+" total names: "+one("select count(*) from IMAGE_V",d));
      var st=db(d).createStatement(); var rs=st.executeQuery("select IMAGE_NAME, IMAGE from IMAGE_V where IMAGE_NAME = "+q(guess));
      var hit=rs.next(); if(!hit){ rs.close(); rs=st.executeQuery("select IMAGE_NAME, IMAGE from IMAGE_V where IMAGE_NAME like "+q(guess+"%")); hit=rs.next(); }
      if(hit){ var nm=trim(rs.getString(1)), b=rs.getBytes(2), hex="", asc="";
        for(var i=0;i<Math.min(24,b.length);i++){ var v=b[i]&255; hex+=(v<16?"0":"")+v.toString(16)+" "; asc+=(v>31&&v<127)?String.fromCharCode(v):"."; }
        print("  blob ["+nm+"] "+b.length+" bytes; head: "+hex+" |"+asc+"|");
        if(OUT){ new File(OUT).mkdirs(); var fo=new FileOutputStream(OUT+"/probe_image.bin"); fo.write(b); fo.close(); print("  wrote "+OUT+"/probe_image.bin"); }
      } else print("  no blob for the guessed name in "+d);
      rs.close(); st.close();
    }catch(e){ print("  IMAGE_V in "+d+": ERROR "+e); }
  });
});

// 5. SA / option parts reachable from this catalog
step("5. SA parts", function(){
  var idx=rows("select SANUM, GROUPNUM from BM_SAIDX_V where CATNUM="+q(CATNUM));
  var sas={}; idx.forEach(function(r){ sas[r.SANUM]=1; });
  print("  BM_SAIDX_V rows "+idx.length+", distinct SANUM "+Object.keys(sas).length+"  e.g. "+Object.keys(sas).slice(0,8).join(" "));
  var s=Object.keys(sas)[0]; if(!s) return;
  show(rows("select SANUM, TEXT, CODEONE, CODETWO from SA_DESCS_V join SA_DICTIONARY_V using (DESCIDX) where LANG="+q(LANG)+" and SANUM="+q(s),3));
  show(rows("select SANUM, SUBGRP, TEXT from sa_subgrp_V where SANUM="+q(s)+" and LANG="+q(LANG),5));
  print("  SA_PARTS_V rows for "+s+": "+one("select count(*) from SA_PARTS_V where SANUM="+q(s)));
  show(rows("select SUBGRP, ITEMNO, PARTTYP, PARTNUM, QUANTSA, STROKE, NOUNIDX, PARTREM from SA_PARTS_V where SANUM="+q(s)+" order by SEQNO",6));
  show(rows("select SANUM, MODEL, STRKVER, SUBMOD, TEXT from sa_strokes_v join SA_DICTIONARY_V using (DESCIDX) where LANG="+q(LANG)+" and SANUM="+q(s),5));
  print("  SA_NPG_V rows for "+s+": "+one("select count(*) from SA_NPG_V where SANUM="+q(s)));
});

// 6. scale: catalogs and classes
step("6. catalogs", function(){
  print("  APPL_CAT_BASE_V rows: "+one("select count(*) from APPL_CAT_BASE_V"));
  var m=rows("select unique catnum, class, aggtype from models_v where catnum <> '598'");
  var byClass={}; m.forEach(function(r){ var k="class "+r.CLASS+" / "+r.AGGTYPE; byClass[k]=byClass[k]||{}; byClass[k][r.CATNUM]=1; });
  Object.keys(byClass).sort().forEach(function(k){ print("  "+k+": "+Object.keys(byClass[k]).length+" catalogs"); });
  show(rows("select * from APPL_CAT_BASE_V where CATNUM="+q(CATNUM),2));
  print("  BM_PARTS2_V total rows: "+one("select count(*) from BM_PARTS2_V"));
  print("  SA_PARTS_V total rows: "+one("select count(*) from SA_PARTS_V"));
});

// 7. images, round 2: which image DB holds a catalog's B-names, and in what format
function head(b){ var hex="", asc=""; for(var i=0;i<Math.min(16,b.length);i++){ var v=b[i]&255; hex+=(v<16?"0":"")+v.toString(16)+" "; asc+=(v>31&&v<127)?String.fromCharCode(v):"."; } return hex+"|"+asc+"|"; }
step("7. image databases", function(){
  var npg=rows("select GROUPNUM, SUBGRP, SEQNO, RESTIMG, IMGTYPE, ARCHIVED from BM_NPG_V where CATNUM="+q(CATNUM)+" order by GROUPNUM, SEQNUM",3);
  show(npg);
  var arch={}; rows("select ARCHIVED from BM_NPG_V where CATNUM="+q(CATNUM)).forEach(function(r){ arch[r.ARCHIVED]=(arch[r.ARCHIVED]||0)+1; });
  print("  ARCHIVED values for catalog: "+JSON.stringify(arch));
  var n=npg[0], pre=n.IMGTYPE+n.GROUPNUM+n.SUBGRP;
  ["BM_IMAGES","BM_IMAGES_ARC","PNG_IMAGES","PNG_IMAGES_ARC","SA_IMAGES"].forEach(function(d){
    try{
      var st=db(d).createStatement(); st.setMaxRows(1); var rs=st.executeQuery("select * from IMAGE_V"), md=rs.getMetaData(), cols=[];
      for(var i=1;i<=md.getColumnCount();i++) cols.push(md.getColumnName(i)+":"+md.getColumnTypeName(i)); rs.close(); st.close();
      print("  "+d+": IMAGE_V cols "+cols.join(", ")+"; rows "+one("select count(*) from IMAGE_V",d));
      print("    sample names: "+rows("select IMAGE_NAME from IMAGE_V",6,d).map(function(x){return "["+x.IMAGE_NAME+"]";}).join(" "));
      var hits=rows("select IMAGE_NAME from IMAGE_V where IMAGE_NAME like "+q(pre+"%"),8,d);
      print("    names like "+pre+"*: "+hits.map(function(x){return "["+x.IMAGE_NAME+"]";}).join(" "));
      if(hits.length){ var st2=db(d).createStatement(), rs2=st2.executeQuery("select IMAGE from IMAGE_V where IMAGE_NAME = "+q(hits[0].IMAGE_NAME));
        if(rs2.next()){ var b=rs2.getBytes(1); print("    blob ["+hits[0].IMAGE_NAME+"] "+b.length+" bytes: "+head(b));
          if(OUT){ new File(OUT).mkdirs(); var fo=new FileOutputStream(OUT+"/"+d+"_"+hits[0].IMAGE_NAME.replace(/[^A-Za-z0-9.]/g,"_")+".bin"); fo.write(b); fo.close(); } }
        rs2.close(); st2.close(); }
    }catch(e){ print("  "+d+": ERROR "+(""+e).split("\n").pop()); }
  });
});

// 8. QUANTBM on real rows: slot layout against SUBMODS
step("8. QUANTBM on real parts", function(){
  var r=rows("select GROUPNUM, SUBGRP, CALLOUT, PARTTYPE, PARTNUM, QUANTBM, SUBMODS, CODEB, STEERING, INDENT, FOOTNOTES, PARTREM, OPTFLAG, REPTYPE, REPPNO from BM_PARTS2_V where CATNUM="+q(CATNUM)+" and PARTNUM not like '%XXXXX%' and PARTNUM not like '%YYYYY%' order by GROUPNUM, SUBGRP, SEQNUM",40);
  r.forEach(function(x){ print("  "+x.GROUPNUM+"/"+x.SUBGRP+" "+x.CALLOUT+" "+x.PARTTYPE+x.PARTNUM+" Q["+x.QUANTBM+"] S["+x.SUBMODS+"] codeb["+x.CODEB+"] steer["+x.STEERING+"] ind["+x.INDENT+"] fn["+x.FOOTNOTES+"] rem["+x.PARTREM+"] opt["+x.OPTFLAG+"] rep["+x.REPTYPE+x.REPPNO+"]"); });
  var digits=one("select count(*) from BM_PARTS2_V where CATNUM="+q(CATNUM)+" and QUANTBM like '%0%'");
  print("  rows whose QUANTBM contains a digit 0: "+digits);
  show(rows("select unique QUANTBM from BM_PARTS2_V where CATNUM="+q(CATNUM)+" and QUANTBM like '%0%' and PARTNUM not like '%YYYYY%'",15));
});

// 9. SA parts with the CHAR(6) SANUM padded the way the server pads it
step("9. SA parts (padded SANUM)", function(){
  var idx=rows("select SANUM, GROUPNUM, CODEONE, CODETWO from BM_SAIDX_V where CATNUM="+q(CATNUM),400);
  function pad(s){ return s.length==5?" "+s:s; }
  var withParts=0, total=0, shown=0;
  idx.forEach(function(x){ var c=+one("select count(*) from SA_PARTS_V where SANUM="+q(pad(x.SANUM))); total+=c; if(c) withParts++;
    if(c && shown<2){ shown++; print("  SA "+x.SANUM+" (group "+x.GROUPNUM+", codes "+x.CODEONE+"/"+x.CODETWO+"): "+c+" parts");
      show(rows("select SANUM, TEXT from SA_DESCS_V join SA_DICTIONARY_V using (DESCIDX) where LANG="+q(LANG)+" and SANUM="+q(pad(x.SANUM)),2));
      show(rows("select SUBGRP, TEXT from sa_subgrp_V where SANUM="+q(pad(x.SANUM))+" and LANG="+q(LANG),3));
      show(rows("select SUBGRP, ITEMNO, PARTTYP, PARTNUM, QUANTSA, STROKE, NOUNIDX, PARTREM from SA_PARTS_V where SANUM="+q(pad(x.SANUM))+" order by SEQNO",5));
      show(rows("select MODEL, STRKVER, SUBMOD, TEXT from sa_strokes_v join SA_DICTIONARY_V using (DESCIDX) where LANG="+q(LANG)+" and SANUM="+q(pad(x.SANUM)),4));
      print("    SA_NPG_V rows: "+one("select count(*) from SA_NPG_V where SANUM="+q(pad(x.SANUM)))); } });
  print("  SAs for catalog: "+idx.length+" index rows, "+withParts+" with parts, "+total+" SA part rows");
});

for(var k in dbs) dbs[k].close();
print("\nPROBE DONE");
