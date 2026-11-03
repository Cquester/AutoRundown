// Decoder for EPC part footnotes, loaded by pull_epc.js. Kept apart so it can
// be checked offline on saved rows without touching the database.
//
// A part row's FOOTNOTES (BM) / FOOTNOT (SA) is a run of 3-character footnote
// numbers ("002003"); the texts are per (catalog, group) in BM_FOOTNOTES_V, per
// SA in SA_FOOTNOTES_V, one dictionary row per line (BM/SA_FOOTNOTES_
// DICTIONARY_V, LANG = the language or 'N' for language-neutral).
//
// Three shapes (probe step 10, 2026-10-15; the third probe step 11, 2026-11-02):
// - old-revision serial footnotes: ABBR "#" + A(from)/B(up to) + the unit
//   letter, TEXT the ident ("F 496808"), one row per plant;
// - old-style text, e.g. "UP TO CHASSIS:" then "100,102,103,110,112 126027"
//   (baumuster suffixes and the chassis number), used as written;
// - revision 9 serial footnotes: ABBR "E" + A(from)/B(up to) + the unit letter,
//   TEXT = ident[0:8] date[8:16] model-year[16:21] P[21] M[22] extra idents[23:]
//   (decompiled FootnoteRev9Serial.setValues/getFirstColumnString). A one-
//   character ident is a plant code only: the change is by date at that plant.
//   P: also valid for the extra idents; M: not valid for them.

var FN_UNITS = {A: "cab", F: "chassis", M: "engine", G: "transmission", L: "steering",
                V: "front axle", H: "rear axle", Z: "transfer case", P: "platform"};
var FN_MAX = 700;                 // characters per footnote in the record

function fnDate(s) { return /^\d{8}$/.test(s) ? s.substr(0, 4) + "-" + s.substr(4, 2) + "-" + s.substr(6, 2) : ""; }

function fnIdents(s, max) {
  s = (s || "").replace(/\s+/g, "");
  var out = [];
  for (var i = 0; i + 8 <= s.length; i += 8) out.push(s.substr(i, 8));
  if (out.length > max) return out.slice(0, max).join(", ") + " ... (" + (out.length - max) + " more)";
  return out.join(", ");
}

// one revision-9 serial row -> "up to engine 32946334 (change date 2015-06-11)", or null
function fnSerial(abbr, text) {
  var m = /^E([AB])([A-Z])$/.exec((abbr || "").replace(/\s+/g, ""));
  if (!m || !text) return null;
  var dir = m[1] == "A" ? "from" : "up to", unit = FN_UNITS[m[2]] || ("unit " + m[2]);
  var units = unit == "chassis" ? unit : unit + "s";   // not "chassiss"
  var ident = text.substr(0, 8).replace(/\s+/g, ""), date = fnDate(text.substr(8, 8));
  var my = text.length > 16 ? text.substr(16, 5).replace(/\s+/g, "") : "";
  var s;
  if (ident.length <= 1) s = dir + " " + units + " built " + (date || "?") + (ident ? " (plant " + ident + ")" : "");
  else s = dir + " " + unit + " " + ident + (date ? " (change date " + date + ")" : "");
  if (my) s += ", model year code " + my;
  var extra = text.length > 23 ? fnIdents(text.substr(23), 30) : "";
  if (extra && text.charAt(21) == "P") s += "; also " + units + " " + extra;
  if (extra && text.charAt(22) == "M") s += "; not " + units + " " + extra;
  return s;
}

// one old-revision serial row -> "up to chassis F 496808", or null. ABBR "#" +
// A(from)/B(up to) + the unit letter, TEXT the ident as printed ("A 109999",
// "016134", a range "025406-227295"); several rows of one footnote are one per
// plant (probe step 11, 2026-11-02: W201 catalog 14A group 67; before, only
// "F 496808" reached the record, without its direction)
function fnOldSerial(abbr, text) {
  var m = /^#([AB])([A-Z])$/.exec((abbr || "").replace(/\s+/g, ""));
  var ident = (text || "").replace(/^\s+|\s+$/g, "").replace(/\s{2,}/g, " ");
  if (!m || !ident) return null;
  return (m[1] == "A" ? "from" : "up to") + " " + (FN_UNITS[m[2]] || ("unit " + m[2])) + " " + ident;
}

// rows {FTNTNUM, REVVER, ABBR, SEQNUM, CONTINUED, TEXT} ordered by FTNTNUM,
// SEQNUM -> {"003": "up to engine ...", ...}. TEXT must not be trimmed at the
// front: the serial fields are positional.
function fnDecode(rows) {
  var lines = {}, lastOld = {};
  rows.forEach(function (r) {
    var num = (r.FTNTNUM || "").replace(/\s+/g, "");
    if (!num) return;
    var raw = r.TEXT == null ? "" : String(r.TEXT).replace(/\s+$/, "");
    var old = fnOldSerial(r.ABBR, raw), abbr = (r.ABBR || "").replace(/\s+/g, "");
    var l0 = lines[num];
    // the same old-style condition at another plant: "up to chassis A 444135, F 496808"
    if (old && l0 && l0.length && lastOld[num] == abbr) { l0[l0.length - 1] += ", " + raw.replace(/^\s+/, ""); return; }
    lastOld[num] = old ? abbr : "";
    var s = old || fnSerial(r.ABBR, raw);
    if (s == null) s = raw.replace(/^\s+/, "").replace(/\s{2,}/g, " ");
    if (!s || /^[=\-_*]+$/.test(s)) return;          // ruler lines in text tables
    var l = lines[num] || (lines[num] = []);
    var main = s.split("; ")[0];
    if (l.length && String(r.CONTINUED) == "1") l[l.length - 1] += " " + s;
    // a serial footnote's "also" and "not" lists come as two rows with the same
    // main clause: keep the clause once
    else if (l.length && main != s && l[l.length - 1].indexOf(main) == 0) l[l.length - 1] += s.substr(main.length);
    else l.push(s);
  });
  var out = {};
  Object.keys(lines).forEach(function (num) {
    var l = lines[num], t = "";
    for (var i = 0; i < l.length; i++)               // "UP TO CHASSIS:" + its lines
      t += (i == 0 ? "" : /:$/.test(l[i - 1]) ? " " : "; ") + l[i];
    out[num] = t.length > FN_MAX ? t.substr(0, FN_MAX) + " ..." : t;
  });
  return out;
}

// "002003" -> ["002", "003"]
function fnRefs(s) {
  var out = [];
  s = (s || "").replace(/\s+$/, "");
  for (var i = 0; i + 3 <= s.length; i += 3) { var c = s.substr(i, 3).replace(/\s+/g, ""); if (c && out.indexOf(c) < 0) out.push(c); }
  return out;
}
