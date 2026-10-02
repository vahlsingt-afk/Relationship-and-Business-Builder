const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, LevelFormat, BorderStyle,
  WidthType, ShadingType, PageNumber
} = require("docx");

const input = process.argv[2];
const output = process.argv[3];
const lines = fs.readFileSync(input, "utf8").split(/\r?\n/);
const contentWidth = 10080;
const blue = "17365D";
const lightBlue = "D9EAF7";
const gray = "666666";
const border = { style: BorderStyle.SINGLE, size: 2, color: "B8C4CE" };
const borders = { top: border, bottom: border, left: border, right: border };

function runs(text, options = {}) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g).filter(Boolean);
  return parts.map(part => {
    if (part.startsWith("**") && part.endsWith("**")) return new TextRun({ text: part.slice(2, -2), bold: true, ...options });
    if (part.startsWith("`") && part.endsWith("`")) return new TextRun({ text: part.slice(1, -1), font: "Aptos Mono", color: "444444", ...options });
    if (part.startsWith("*") && part.endsWith("*")) return new TextRun({ text: part.slice(1, -1), italics: true, ...options });
    return new TextRun({ text: part, ...options });
  });
}

function para(text, opts = {}) {
  return new Paragraph({
    children: runs(text),
    spacing: { after: opts.after ?? 120, line: 276 },
    keepNext: opts.keepNext,
    numbering: opts.numbering,
  });
}

function cell(text, width, header = false) {
  return new TableCell({
    width: { size: width, type: WidthType.DXA }, borders,
    shading: header ? { fill: blue, type: ShadingType.CLEAR } : undefined,
    margins: { top: 90, bottom: 90, left: 120, right: 120 },
    children: [new Paragraph({
      children: runs(text, header ? { bold: true, color: "FFFFFF" } : { size: 19 }),
      spacing: { after: 0 }
    })]
  });
}

const children = [];
let i = 0;
while (i < lines.length) {
  const raw = lines[i];
  const line = raw.trim();
  if (!line || line === "---") { i++; continue; }
  if (line.startsWith("|")) {
    const tableLines = [];
    while (i < lines.length && lines[i].trim().startsWith("|")) tableLines.push(lines[i++].trim());
    const parsed = tableLines
      .filter(x => !/^\|?[\s:|-]+\|?$/.test(x))
      .map(x => x.replace(/^\||\|$/g, "").split("|").map(v => v.trim()));
    if (parsed.length) {
      const count = parsed[0].length;
      const widths = count === 5 ? [1700, 1800, 1700, 1700, 3180] : Array(count).fill(Math.floor(contentWidth / count));
      widths[widths.length - 1] += contentWidth - widths.reduce((a,b) => a+b, 0);
      children.push(new Table({
        width: { size: contentWidth, type: WidthType.DXA }, columnWidths: widths,
        rows: parsed.map((row, ri) => new TableRow({
          tableHeader: ri === 0,
          children: row.map((value, ci) => cell(value, widths[ci], ri === 0))
        }))
      }));
      children.push(new Paragraph({ spacing: { after: 120 } }));
    }
    continue;
  }
  if (line.startsWith("# ")) {
    children.push(new Paragraph({ heading: HeadingLevel.TITLE, children: runs(line.slice(2)), spacing: { after: 120 } }));
  } else if (line.startsWith("## ")) {
    children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: runs(line.slice(3)), pageBreakBefore: line === "## Technology Environment", keepNext: true }));
  } else if (line.startsWith("### ")) {
    children.push(new Paragraph({ heading: HeadingLevel.HEADING_2, children: runs(line.slice(4)), keepNext: true }));
  } else if (/^- /.test(line)) {
    children.push(para(line.slice(2), { numbering: { reference: "bullets", level: 0 } }));
  } else if (/^\d+\. /.test(line)) {
    children.push(para(line.replace(/^\d+\. /, ""), { numbering: { reference: "numbers", level: 0 } }));
  } else {
    children.push(para(line));
  }
  i++;
}

const doc = new Document({
  creator: "Relationship & Business Builder",
  title: "Del Taco Background Brief",
  description: "Account background and discovery preparation",
  styles: {
    default: { document: { run: { font: "Aptos", size: 21, color: "202020" } } },
    paragraphStyles: [
      { id: "Title", name: "Title", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: "Aptos Display", size: 40, bold: true, color: blue },
        paragraph: { spacing: { before: 0, after: 120 }, outlineLevel: 0 } },
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: "Aptos Display", size: 29, bold: true, color: blue },
        paragraph: { spacing: { before: 280, after: 100 }, keepNext: true, outlineLevel: 0,
          border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: lightBlue, space: 4 } } } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: "Aptos", size: 24, bold: true, color: "2F5597" },
        paragraph: { spacing: { before: 200, after: 80 }, keepNext: true, outlineLevel: 1 } },
    ]
  },
  numbering: { config: [
    { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } }] },
    { reference: "numbers", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } }] }
  ] },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1080, right: 1080, bottom: 1080, left: 1080 } } },
    headers: { default: new Header({ children: [new Paragraph({
      children: [new TextRun({ text: "DEL TACO  |  ACCOUNT BACKGROUND BRIEF", bold: true, color: gray, size: 16 })],
      border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "D9E2F3", space: 4 } }
    })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [
      new TextRun({ text: "Confidential  •  September 4, 2026  •  Page ", color: gray, size: 16 }),
      new TextRun({ children: [PageNumber.CURRENT], color: gray, size: 16 })
    ] })] }) },
    children
  }]
});

Packer.toBuffer(doc).then(buffer => fs.writeFileSync(output, buffer));
