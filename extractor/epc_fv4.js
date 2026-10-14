// Decoder for EPC "FV4" parts drawings, loaded by pull_epc.js. Kept apart so
// it can be checked offline on a saved blob without touching the database.
// decodeFV4(javaByteArray) -> {img: BufferedImage 1024x1280, rows, hdrRows} or null.
var BufferedImage=Java.type("java.awt.image.BufferedImage");

// Port of the EPC client's ImageExtractorFV4 (Snap-on's own run-length format:
// 1024 px wide, up to 1280 rows, 1/2/4 bit planes). Faithful line for line,
// including the client's output palette (indexed image, values 0/229/242/255).
var codeBits=[1,1,1,1,1,1,1,1,2,2,2,2,3,3,4,5], rightShift=[30,30,30,30,30,30,30,30,28,28,28,28,26,26,24,22];
var codeBase=[0,0,0,0,0,0,0,0,4,4,4,4,20,20,84,340];
function zeros(n){ var a=new Array(n); for(var i=0;i<n;i++) a[i]=0; return a; }
function decodeFV4(buf){
  var n=buf.length; if(n<64) return null;
  var bpp=buf[35], cols=((buf[38]&255)<<8)+(buf[39]&255), hdrRows=((buf[40]&255)<<8)+(buf[41]&255), overlays=buf[45];
  if((bpp!=1&&bpp!=2&&bpp!=4)||cols!=1024||hdrRows>1280||overlays>2) return null;
  var off=46, rOff=0, rLen=0, aOff=0, aLen=0;
  while(overlays-- > 0){
    if(buf[off]==1&&buf[off+1]==1){ rOff=((buf[off+6]&255)<<8)+(buf[off+7]&255); rLen=((buf[off+10]&255)<<8)+(buf[off+11]&255); }
    else if(buf[off]==32&&buf[off+1]==1){ aOff=((buf[off+6]&255)<<8)+(buf[off+7]&255); aLen=((buf[off+10]&255)<<8)+(buf[off+11]&255); }
    off+=16;
  }
  if(rOff==0||rOff+rLen>n||buf[rOff]!=1||buf[rOff+1]!=4) return null;
  var pos=(rOff>aOff ? rOff+rLen-1 : aOff+aLen-1); pos=Math.floor((pos+1023)/1024)*1024;
  var img=new BufferedImage(1024,1280,BufferedImage.TYPE_BYTE_INDEXED), data=img.getRaster().getDataBuffer().getData();
  var st={row:0};
  try{
    while(pos<n){ var c=decodeChunk(buf,pos,n-pos,bpp,data,st); if(c<0) return null; if(c==0) break; pos+=c; }
  }catch(e){ /* the client also keeps whatever decoded before an error */ }
  // crop the blank (white = index 255 = -1) rows at the bottom of the page
  var last=st.row-1;
  while(last>=0){ var o=last*1024, blank=true; for(var i=0;i<1024;i++) if(data[o+i]!=-1){ blank=false; break; } if(!blank) break; last--; }
  return {img:img, rows:st.row, hdrRows:hdrRows, contentRows:Math.min(st.row,last+17)};
}
function decodeChunk(buf,bo,bb,bpp,data,st){
  var n=buf.length, p, planesOdd=[], planesEven=[];
  for(p=0;p<bpp;p++){ planesOdd.push(zeros(1026)); planesEven.push(zeros(1026)); }
  var pal=zeros(1025);
  var sgBits=((buf[bo+6]&255)<<8)+(buf[bo+5]&255), sgBytes=(sgBits+7)>>3;
  if(sgBytes+16>bb) return 0;
  var startRow=((buf[bo+1]&255)<<8)+(buf[bo]&255);
  if(buf[bo+4]!=bpp||startRow!=st.row) return 0;
  var sgRows=((buf[bo+3]&255)<<8)+(buf[bo+2]&255);
  var bi=bo+16, value=(buf[bi++]&255)<<24; value|=(buf[bi++]&255)<<16; value|=(buf[bi++]&255)<<8; value|=buf[bi++]&255;
  var bitCount=0, odd=false, index, t, t2;
  while(sgRows-- > 0){
    odd=!odd;
    var mask=1<<(bpp-1);
    for(p=0;p<bpp;p++){
      var cur=odd?planesOdd[p]:planesEven[p], prev=odd?planesEven[p]:planesOdd[p];
      var toggle=false, toggleSave=false, ic=0, ip=0, column=0;
      while(column<1024){
        if((value & 0xE0000000) == -536870912){
          index=(value & 0x1E000000)>>25; t=codeBits[index]; bitCount+=3+t;
          value<<=3+t; var run=value>>>rightShift[index];
          t2=t<<1; value<<=t2; bitCount+=t2;
          column+=run+codeBase[index];
        } else {
          var rel;
          if((value & 0x80000000)==0){ rel=0; value<<=1; bitCount++; }
          else if((value & 0x40000000)==0){ rel=(value & 0x20000000)==0 ? 1 : -1; value<<=3; bitCount+=3; }
          else {
            var sign=value & 0x10000000; index=(value & 0x0F000000)>>24; t=codeBits[index]; bitCount+=4+t;
            value<<=4+t; rel=(value>>>rightShift[index])+codeBase[index]+2; if(sign!=0) rel=-rel;
            t2=t<<1; value<<=t2; bitCount+=t2;
          }
          if(toggle!=toggleSave) ip = ip==0 ? ip+1 : ip-1;
          t = ic==0 ? 0 : cur[ic-1];
          while(prev[ip] <= t){ ip+=2; if(ip>=1026) throw "fv4: reference row overrun"; }
          column=prev[ip]+rel;
          toggleSave=toggle;
        }
        if(ic>=1026 || column<0 || column>1024) throw "fv4: bad run";
        cur[ic++]=column;
        pal[column]|=mask;
        if(bitCount>=8){ t=0; while(bitCount>=8){ bitCount-=8; sgBits-=8; if(bi>=n) break; t=(t<<8)|(buf[bi++]&255); } value|=t<<bitCount; }
        toggle=!toggle;
      }
      if(column!=1024) return -1;
      cur[ic++]=column; cur[ic++]=column; pal[column]|=mask; mask>>=1;
    }
    mask=(1<<bpp)-1;
    if(pal[0]!=0) mask=pal[0]^mask;
    pal[0]=0;
    var o=st.row*1024;
    for(var i=1;i<=1024;i++){
      if(mask==0) data[o]=0; else if(mask==1) data[o]=-27; else if(mask==2) data[o]=-14; else if(mask==3) data[o]=-1;
      if(pal[i]!=0){ mask=pal[i]^mask; pal[i]=0; }
      o++;
    }
    st.row++;
  }
  return Math.floor((sgBytes+1023)/1024)*1024;
}

