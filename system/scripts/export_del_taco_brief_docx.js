const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, LevelFormat, BorderStyle,
  WidthType, ShadingType, PageNumber
} = require("docx");

const input = process.argv[2];
const output = process.argv[3];
const lines = fs.readFileSync(input, "utf8").split(/\r?\n/);
const children = [];
const border = { style: BorderStyle.SINGLE, size: 1, color: "D9E2F3" };
const borders = { top: border, bottom: border, left: border, right: border };

function runs(text) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g).filter(Boolean);
  return parts.map((part) => {
    if (part.startsWith("**") && part.endsWith("**")) return new TextRun({ text: part.slice(2, -2), bold: true });
    if (part.startsWith("`") && part.endsWith("`")) return new TextRun({ text: part.slice(1, -1), font: "Courier New", size: 19 });
    if (part.startsWith("*") && part.endsWith("*")) return new TextRun({ text: part.slice(1, -1), italics: true });
    return new TextRun(part);
  });
}

function cell(text, width, header = false) {
  return new TableCell({
    width: { size: width, type: WidthType.DXA }, borders,
    shading: header ? { fill: "DCE6F1", type: ShadingType.CLEAR } : undefined,
    margins: { top: 80, bottom: 80, left: 110, right: 110 },
    children: [new Paragraph({ children: [new TextRun({ text: text.trim(), bold: header, size: 19 })] })]
  });
}

for (let i = 0; i < lines.length; i++) {
  const line = lines[i].trimEnd();
  if (!line.trim() || line === "---") continue;
  if (line.startsWith("|")) {
    const block = [];
    while (i < lines.length && lines[i].trim().startsWith("|")) block.push(lines[i++].trim());
    i--;
    const rows = block.filter((r) => !/^\|[\s:|-]+\|$/.test(r)).map((r) => r.slice(1, -1).split("|"));
    if (rows.length) {
      const cols = rows[0].length;
      const widths = Array(cols).fill(Math.floor(9360 / cols));
      widths[cols - 1] += 9360 - widths.reduce((a, b) => a + b, 0);
      children.push(new Table({
        width: { size: 9360, type: WidthType.DXA }, columnWidths: widths,
        rows: rows.map((r, ri) => new TableRow({ children: r.map((v, ci) => cell(v, widths[ci], ri === 0)) }))
      }));
    }
    continue;
  }
  if (line.startsWith("# ")) {
    children.push(new Paragraph({ heading: HeadingLevel.TITLE, spacing: { after: 180 }, children: runs(line.slice(2)) }));
  } else if (line.startsWith("## ")) {
    children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: runs(line.slice(3)) }));
  } else if (line.startsWith("### ")) {
    children.push(new Paragraph({ heading: HeadingLevel.HEADING_2, children: runs(line.slice(4)) }));
  } else if (/^- /.test(line)) {
    children.push(new Paragraph({ numbering: { reference: "bullets", level: 0 }, children: runs(line.slice(2)) }));
  } else {
    children.push(new Paragraph({ spacing: { after: 120 }, children: runs(line) }));
  }
}

const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 21, color: "222222" }, paragraph: { spacing: { line: 276 } } } },
    paragraphStyles: [
      { id: "Title", name: "Title", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: "Arial", size: 38, bold: true, color: "1F4E79" }, paragraph: { spacing: { after: 240 }, outlineLevel: 0 } },
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: "Arial", size: 28, bold: true, color: "1F4E79" }, paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: "Arial", size: 23, bold: true, color: "365F91" }, paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 1 } }
    ]
  },
  numbering: { config: [{ reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } }] }] },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1080, right: 1080, bottom: 1080, left: 1080 } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [new TextRun({ text: "INTERNAL — DEL TACO ACCOUNT BRIEF", bold: true, size: 16, color: "6B7280" })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Global Payments / Genius  |  Page ", size: 16, color: "6B7280" }), new TextRun({ children: [PageNumber.CURRENT], size: 16, color: "6B7280" })] })] }) },
    children
  }]
});

Packer.toBuffer(doc).then((buffer) => fs.writeFileSync(output, buffer));
