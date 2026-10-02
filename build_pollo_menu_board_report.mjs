import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const inputPath = "/Users/toddvahlsing/Documents/Genius Info/Accounts/Pollo Campero/Copy of STOCK Menu Boards RFP.xlsx";
const outputDir = "/Users/toddvahlsing/Documents/Claude/Projects/Relationship & Business Builder/outputs/01a0b457-2609-7632-a292-c6dff2d324df";
const outputPath = `${outputDir}/Pollo_Campero_Digital_Menu_Board_Stock_Report.xlsx`;

const input = await FileBlob.load(inputPath);
const sourceWb = await SpreadsheetFile.importXlsx(input);
const inv = sourceWb.worksheets.getItem("Inventory");
const used = inv.getUsedRange(true).values;
const headers = used[0].map(v => String(v ?? ""));
const idx = Object.fromEntries(headers.map((h, i) => [h, i]));
const normalizeModel = (v) => {
  if (v == null || String(v).trim() === "") return "MODEL MISSING";
  return String(v).trim()
    .replace(/\s+(?:2\.0|3\.0|3\.2|4\.0|4\.1|6\.0|9\.0)$/i, "")
    .replace(/\.AUSCLJR/i, "");
};

const groups = new Map();
for (const row of used.slice(1)) {
  const model = normalizeModel(row[idx.Model]);
  const qty = Number(row[idx.Qty] ?? 0);
  if (!groups.has(model)) groups.set(model, { model, total: 0, active: 0, stock: 0, newQty: 0, usedQty: 0, indoor: 0, outdoor: 0, raw: new Set(), makes: new Set() });
  const g = groups.get(model);
  g.total += qty;
  if (row[idx.Status] === "Active") g.active += qty;
  if (row[idx.Status] === "In Stock") g.stock += qty;
  if (row[idx.Condition] === "New") g.newQty += qty;
  if (row[idx.Condition] === "Used") g.usedQty += qty;
  if (row[idx.Type] === "Indoor") g.indoor += qty;
  if (row[idx.Type] === "Outdoor") g.outdoor += qty;
  if (row[idx.Model] != null && String(row[idx.Model]).trim()) g.raw.add(String(row[idx.Model]).trim());
  if (row[idx.Make] != null && String(row[idx.Make]).trim()) g.makes.add(String(row[idx.Make]).trim());
}

const sources = {
  SM3B: [2015, "webOS 2.0", "Yes — built-in quad-core SoC", "https://solutions.lg.com/us/digital-signage/lg-22SM3B"],
  SM3G: [2020, "webOS 4.1", "Yes — built-in quad-core SoC", "https://www.lg.com/ca_en/business/digital-signage/standard-digital-signage/22sm3g-b/"],
  SM5KB: [2015, "webOS 2.0", "Yes — built-in SoC", "https://www.lg.com/us/business/download/resources/BT00001837/BT00001837_1051.pdf"],
  SM5KC: [2016, "webOS 3.0", "Yes — built-in SoC", "https://www.lg.com/global/business/download/resources/id/01_Digital_Signage/SM5KC_low.pdf"],
  SM5KD: [2017, "webOS 3.2", "Yes — built-in quad-core SoC", "https://www.lg.com/us/business/download/resources/BT00001837/LG_SPEC-SHEET_SM5KD_031834_PR%20%281%29.pdf"],
  SM5KE: [2018, "webOS 4.0", "Yes — built-in SoC", "https://www.lg.com/us/business/download/resources/BT00001837/LG_SPEC-SHEET_SM5KE_061823_PR.pdf"],
  SM5J: [2021, "webOS 6.0", "Yes — built-in SoC", "https://solutions.lg.com/us/digital-signage/lg-32sm5j-b"],
  UM3DG: [2019, "webOS 4.1", "Yes — built-in SoC", "https://solutions.lg.com/us/digital-signage/lg-49um3dg-b"],
  UL3J: [2021, "webOS 6.0", "Yes — built-in SoC", "https://www.lg.com/us/business/download/resources/CT00001837/LG_SPEC-SHEET_UL3J-Series_072235_LR%5B20230412_004940%5D.pdf"],
  UH5J: [2022, "webOS 6.0", "Yes — built-in SoC", "https://www.lg.com/us/business/download/resources/CT00001837/LG_SPEC-SHEET_UH5J%20Series_0423SK_LR%5B20230701_065503%5D.pdf"],
  UH5Q: [2024, "webOS 8.0 (inventory says 9.0)", "Yes — built-in high-performance SoC", "https://www.lg.com/global/business/commercial-display/digital-signage/standard/49uh5q-e/"],
  XE4F: [2019, "webOS 4.0", "Yes — built-in SoC / embedded CMS", "https://www.lg.com/us/support/products/documents/LG_SPEC-SHEET_XE4F_071934_PR.pdf"],
  XE3P: [2024, "webOS 6.1", "Yes — built-in SoC", "https://www.lg.com/us/business/digital-signage/lg-55xe3p-b"],
};
const seriesKey = (m) => Object.keys(sources).find(k => m.includes(k));

const rows = [...groups.values()].sort((a,b) => b.total-a.total || a.model.localeCompare(b.model)).map(g => {
  const key = seriesKey(g.model);
  const meta = key ? sources[key] : [null, "Unknown", "Unknown — model number missing", ""];
  const manufacturer = g.makes.size ? [...g.makes].join(", ") : (key ? "LG" : "Unknown");
  const note = g.model === "MODEL MISSING" ? "One outdoor active record has no model number; inspect source inventory row." :
    g.model === "49UH5Q-EQ" ? "Inventory label appears to be 49UH5Q-EQ.AUSCLJR; official family is 49UH5Q-E. Inventory webOS 9.0 conflicts with current official webOS 8.0 listing." :
    g.model === "49XE4F-M" ? "Potential truncation/variant of 49XE4F-MJ; kept separate pending serial/model validation." :
    g.model === "55XE3P" ? "No suffix in source; kept separate from 55XE3P-BP pending label validation." : "";
  return [g.model, manufacturer, g.total, g.active, g.stock, g.newQty, g.usedQty, g.indoor, g.outdoor, meta[0], meta[0] ? 2026-meta[0] : null, meta[1], meta[2], [...g.raw].join("; "), note, meta[3]];
});
const displayRows = rows.map(r => [r[0], r[1], r[2], r[3], r[4], r[7], r[8], r[9], r[10], r[11], r[12].replace(/^Yes — /, "Yes — "), r[14]]);

const wb = Workbook.create();
const summary = wb.worksheets.add("Model Summary");
const data = wb.worksheets.add("Source Inventory");
const refs = wb.worksheets.add("Research Notes");
summary.showGridLines = false; data.showGridLines = false; refs.showGridLines = false;
summary.tabColor = "#8B1E2D";

summary.getRange("A2:L2").merge();
summary.getRange("A2").values = [["Digital Menu Board Inventory"]];
summary.getRange("A3:L3").merge();
summary.getRange("A3").values = [["Pollo Campero  |  September 18, 2026"]];
summary.getRange("A5:B6").values = [["TOTAL DISPLAYS", 1001],["Model variants",rows.length]];
summary.getRange("D5:E6").values = [["DEPLOYED",908],["Indoor",rows.reduce((s,r)=>s+r[7],0)]];
summary.getRange("G5:H6").values = [["IN STOCK",93],["Outdoor",rows.reduce((s,r)=>s+r[8],0)]];
summary.getRange("J5:L6").values = [["SCOPE","All inventory"],["AGE BASIS","Model introduction"]];
const outHeaders = ["Model", "Manufacturer", "Total", "Deployed", "In stock", "Indoor", "Outdoor", "Introduced", "Est. age", "Platform", "Built-in SoC", "Notes"];
summary.getRange("A9:L9").values = [outHeaders];
summary.getRange(`A10:L${9+displayRows.length}`).values = displayRows;
summary.tables.add(`A9:L${9+displayRows.length}`, true, "ModelSummaryTable").style = "TableStyleLight1";
summary.freezePanes.freezeRows(9);

summary.getRange("A2:L2").format = { font:{name:"Arial",size:16,bold:true,color:"#6E1E2B"}, rowHeight:26 };
summary.getRange("A3:L3").format = { font:{name:"Arial",size:10,color:"#666666"}, rowHeight:19, borders:{bottom:{style:"thin",color:"#D8C7C2"}} };
for (const block of ["A5:B6","D5:E6","G5:H6","J5:L6"]) summary.getRange(block).format.borders = {preset:"outside",style:"thin",color:"#D8C7C2"};
for (const block of ["A5:A6","D5:D6","G5:G6","J5:J6"]) { summary.getRange(block).format.font={name:"Arial",size:9,bold:true,color:"#6E1E2B"}; summary.getRange(block).format.fill="#F4E9E3"; }
summary.getRange("A5:L6").format.verticalAlignment="center";
summary.getRange("B5:B5").format.font={name:"Arial",size:14,bold:true,color:"#222222"};
summary.getRange("E5:E5").format.font={name:"Arial",size:14,bold:true,color:"#222222"};
summary.getRange("H5:H5").format.font={name:"Arial",size:14,bold:true,color:"#222222"};
summary.getRange(`A9:L${9+displayRows.length}`).format.font = {name:"Arial",size:10,color:"#262626"};
summary.getRange("A9:L9").format={fill:"#6E1E2B",font:{name:"Arial",size:10,bold:true,color:"#FFFFFF"},verticalAlignment:"center",horizontalAlignment:"center",borders:{insideVertical:{style:"thin",color:"#FFFFFF"}}};
summary.getRange(`C10:G${9+displayRows.length}`).format.numberFormat = "#,##0";
summary.getRange(`H10:I${9+displayRows.length}`).format.numberFormat = "0";
summary.getRange(`H10:I${9+displayRows.length}`).format.horizontalAlignment = "center";
summary.getRange(`J10:J${9+displayRows.length}`).format.borders = {left:{style:"thin",color:"#E2D9D4"}};
summary.getRange(`A10:L${9+displayRows.length}`).format.verticalAlignment = "center";
summary.getRange(`L10:L${9+displayRows.length}`).format.wrapText = true;
for (let r=10;r<=9+displayRows.length;r++) if ((r-10)%2===1) summary.getRange(`A${r}:L${r}`).format.fill="#FAF7F4";
summary.getRange(`A9:L9`).format.rowHeight = 28;
const widths = [18,12,9,10,10,9,9,12,10,22,28,48];
widths.forEach((w,i)=>summary.getRangeByIndexes(0,i,9+displayRows.length,1).format.columnWidth=w);
summary.getRange(`A10:L${9+displayRows.length}`).format.rowHeight = 25;
for (let i=0;i<displayRows.length;i++) if (displayRows[i][11]) summary.getRange(`A${10+i}:L${10+i}`).format.rowHeight = displayRows[i][11].length > 120 ? 52 : 38;
summary.getRange(`L10:L${9+displayRows.length}`).conditionalFormats.add("containsText", {text:"pending",format:{fill:"#FFF2CC",font:{color:"#7F6000",bold:true}}});

data.getRange("A1:I1").values = [headers];
data.getRange(`A2:I${used.length}`).values = used.slice(1);
data.tables.add(`A1:I${used.length}`, true, "SourceInventoryTable").style = "TableStyleMedium2";
data.freezePanes.freezeRows(1);
data.getRange(`A1:I${used.length}`).format.font = {name:"Arial",size:9,color:"#222222"};
data.getRange(`B2:B${used.length}`).format.numberFormat = "#,##0";
data.getRange(`A1:I${used.length}`).format.autofitColumns();
data.getRange("E:E").format.columnWidth = 28; data.getRange("F:F").format.columnWidth = 48; data.getRange("H:H").format.columnWidth = 20; data.getRange("I:I").format.columnWidth = 24;

const refRows = Object.entries(sources).map(([k,v])=>[k,"LG",v[0],2026-v[0],v[1],v[2],v[3]]);
refs.getRange("A2:G2").merge(); refs.getRange("A2").values=[["Model research and methodology"]];
refs.getRange("A4:G4").values=[["Series","Manufacturer","Est. launch year","Approx. age","Platform / OS","SoC assessment","Official source"]];
refs.getRange(`A5:G${4+refRows.length}`).values=refRows;
refs.tables.add(`A4:G${4+refRows.length}`,true,"ResearchSourcesTable").style="TableStyleMedium2";
refs.getRange(`A${6+refRows.length}:G${9+refRows.length}`).values = [
  ["Method note", "Model labels were trimmed and trailing webOS version text was removed for aggregation. Hardware suffixes were preserved."],
  ["Age note", "Approximate age is 2026 minus the estimated model-series introduction year. It is not the age of an individual unit."],
  ["SoC note", "Yes means LG documents an embedded webOS/SoC platform that may run compatible signage software without a separate player. Compatibility still requires app/CMS and firmware validation."],
  ["Data quality", "One unit has no model. Three labels require validation: 49XE4F-M, 55XE3P without suffix, and 49UH5Q-EQ.AUSCLJR with an OS-version discrepancy."]
];
refs.getRange("A2:G2").format={font:{name:"Arial",size:16,bold:true,color:"#222222"},rowHeight:26};
refs.getRange(`A4:G${9+refRows.length}`).format.font={name:"Arial",size:10,color:"#222222"};
refs.getRange(`B${6+refRows.length}:G${9+refRows.length}`).merge(true);
refs.getRange(`A${6+refRows.length}:A${9+refRows.length}`).format.font={name:"Arial",size:10,bold:true,color:"#FFFFFF"};
refs.getRange(`A${6+refRows.length}:A${9+refRows.length}`).format.fill="#8B1E2D";
refs.getRange(`B${6+refRows.length}:G${9+refRows.length}`).format.wrapText=true;
refs.getRange("A:G").format.columnWidth=18; refs.getRange("E:E").format.columnWidth=28; refs.getRange("F:F").format.columnWidth=38; refs.getRange("G:G").format.columnWidth=65;
refs.freezePanes.freezeRows(4);

wb.recalculate();
const check = await wb.inspect({kind:"table",range:`Model Summary!A1:L${9+displayRows.length}`,include:"values,formulas",tableMaxRows:40,tableMaxCols:12,maxChars:15000});
console.log(check.ndjson);
const errors = await wb.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",options:{useRegex:true,maxResults:300},summary:"final formula error scan"});
console.log(errors.ndjson);
await fs.mkdir(outputDir,{recursive:true});
const preview = await wb.render({sheetName:"Model Summary",range:`A1:L${9+displayRows.length}`,scale:1.2,format:"png"});
await fs.writeFile(`${outputDir}/Pollo_Campero_Digital_Menu_Board_Stock_Report_preview.png`,new Uint8Array(await preview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(wb);
await output.save(outputPath);
console.log(outputPath);
