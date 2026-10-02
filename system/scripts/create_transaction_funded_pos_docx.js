const fs = require("fs");
const {
  AlignmentType,
  BorderStyle,
  Document,
  Footer,
  Header,
  HeadingLevel,
  LevelFormat,
  PageBreak,
  PageNumber,
  Packer,
  Paragraph,
  TabStopPosition,
  TabStopType,
  TextRun,
} = require("docx");

const sourcePath = process.argv[2];
const outputPath = process.argv[3];
const memoOnly = process.argv.includes("--memo-only");

if (!sourcePath || !outputPath) {
  throw new Error("Usage: node create_transaction_funded_pos_docx.js <source.md> <output.docx>");
}

const email = {
  to: "Ryan Hildebrand",
  subject: "Concept to explore: transaction-metered POS for Worldpay customers",
  paragraphs: [
    "Ryan,",
    "I have been thinking about whether Worldpay and Genius could create a different way to price POS software for qualified Worldpay customers—a separately stated fixed software cost per eligible card transaction, with a defined monthly floor and ceiling, instead of a separate recurring SaaS invoice.",
    "The potential value is broader than pricing. If our systems can support it, the model could begin collecting with production activity, reduce accounts-receivable and collections work, protect strategic processing relationships, and give Worldpay relationship managers a differentiated alternative when a customer's incumbent POS contract is approaching renewal. Customers would gain cash-flow alignment, price certainty, device flexibility, an API-first platform that preserves their technology choices, and one Genius software foundation for domestic and international operations.",
    "Five Guys may be a useful first design case because Worldpay already has the relationship, its PAR renewal is believed to be approaching in spring 2027, and the account has both global requirements and a strong preference for vendor independence. I do not want to take this concept to M.J. or position it as a customer offer until we have aligned internally.",
    "I would like your perspective on two threshold questions: can we physically, financially, and operationally do this; and does the business have the will to explore something this disruptive? If you believe it merits investigation, the next step would be a focused feasibility review with the right finance, billing, product, legal, risk, implementation, and support leaders.",
    "I attached a short executive summary that lays out the concept, the potential Worldpay/Genius benefits, the Five Guys design case, and the questions we would need to answer. Could we spend 30 minutes reviewing it and deciding whether it deserves an internal feasibility team?",
    "Todd",
  ],
};

function runsWithBold(text) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return parts.map((part) => {
    const bold = part.startsWith("**") && part.endsWith("**");
    return new TextRun({
      text: bold ? part.slice(2, -2) : part,
      bold,
      font: "Arial",
      size: 20,
    });
  });
}

function bodyParagraph(text, options = {}) {
  return new Paragraph({
    children: runsWithBold(text),
    spacing: { after: options.after ?? 105, line: 258 },
    keepNext: options.keepNext ?? false,
  });
}

const markdown = fs.readFileSync(sourcePath, "utf8");
const lines = markdown.split(/\r?\n/);
const memoChildren = [];
let firstTitle = true;
let previousWasNumbered = false;
let numberingGroup = 0;

for (const rawLine of lines) {
  const line = rawLine.trim();
  if (!line) continue;

  if (line.startsWith("# ")) {
    previousWasNumbered = false;
    const title = line.slice(2);
    memoChildren.push(new Paragraph({
      children: [new TextRun({ text: title, bold: true, font: "Arial", size: firstTitle ? 38 : 32, color: "17365D" })],
      spacing: { before: firstTitle ? 0 : 240, after: 180 },
      border: firstTitle ? { bottom: { style: BorderStyle.SINGLE, size: 10, color: "2E75B6", space: 8 } } : undefined,
      keepNext: true,
    }));
    firstTitle = false;
  } else if (line.startsWith("## ")) {
    previousWasNumbered = false;
    memoChildren.push(new Paragraph({
      heading: HeadingLevel.HEADING_1,
      children: [new TextRun(line.slice(3))],
      keepNext: true,
    }));
  } else if (line.startsWith("### ")) {
    previousWasNumbered = false;
    memoChildren.push(new Paragraph({
      heading: HeadingLevel.HEADING_2,
      children: [new TextRun(line.slice(4))],
      keepNext: true,
    }));
  } else if (/^\d+\.\s/.test(line)) {
    if (!previousWasNumbered) numberingGroup += 1;
    memoChildren.push(new Paragraph({
      numbering: { reference: `numbers${numberingGroup}`, level: 0 },
      children: runsWithBold(line.replace(/^\d+\.\s/, "")),
      spacing: { after: 75, line: 250 },
    }));
    previousWasNumbered = true;
  } else if (line.startsWith("- ")) {
    previousWasNumbered = false;
    memoChildren.push(new Paragraph({
      numbering: { reference: "bullets", level: 0 },
      children: runsWithBold(line.slice(2)),
      spacing: { after: 65, line: 250 },
    }));
  } else if (line.startsWith("> ")) {
    previousWasNumbered = false;
    memoChildren.push(new Paragraph({
      children: [new TextRun({ text: line.slice(2).replace(/\*\*/g, ""), italics: true, color: "17365D", font: "Arial", size: 22 })],
      indent: { left: 360, right: 360 },
      border: { left: { style: BorderStyle.SINGLE, size: 14, color: "5B9BD5", space: 10 } },
      spacing: { before: 80, after: 140, line: 276 },
    }));
  } else {
    previousWasNumbered = false;
    memoChildren.push(bodyParagraph(line));
  }
}

const emailChildren = [
  new Paragraph({
    children: [new TextRun({ text: "Introductory Email Draft", bold: true, font: "Arial", size: 38, color: "17365D" })],
    border: { bottom: { style: BorderStyle.SINGLE, size: 10, color: "2E75B6", space: 8 } },
    spacing: { after: 240 },
  }),
  bodyParagraph(`To: ${email.to}`, { after: 70 }),
  new Paragraph({
    children: [new TextRun({ text: "Subject: ", bold: true, font: "Arial", size: 21 }), new TextRun({ text: email.subject, font: "Arial", size: 21 })],
    spacing: { after: 220 },
  }),
  ...email.paragraphs.map((paragraph, index) => bodyParagraph(paragraph, { after: index === email.paragraphs.length - 1 ? 0 : 180 })),
  new Paragraph({ children: [new PageBreak()] }),
];

const doc = new Document({
  creator: "Relationship Builder",
  title: "Worldpay/Genius Transaction-Metered POS Initiative",
  subject: "Internal concept and introductory email",
  description: "Editable executive summary for internal Worldpay/Genius feasibility review.",
  styles: {
    default: { document: { run: { font: "Arial", size: 20, color: "222222" } } },
    paragraphStyles: [
      {
        id: "Heading1",
        name: "Heading 1",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
        run: { font: "Arial", size: 27, bold: true, color: "17365D" },
        paragraph: { spacing: { before: 230, after: 95 }, outlineLevel: 0, keepNext: true },
      },
      {
        id: "Heading2",
        name: "Heading 2",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
        run: { font: "Arial", size: 22, bold: true, color: "2E75B6" },
        paragraph: { spacing: { before: 155, after: 75 }, outlineLevel: 1, keepNext: true },
      },
    ],
  },
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } }],
      },
      {
        reference: "numbers1",
        levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } }],
      },
      {
        reference: "numbers2",
        levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } }],
      },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1080, right: 1080, bottom: 900, left: 1080 },
      },
    },
    headers: {
      default: new Header({ children: [new Paragraph({
        children: [new TextRun({ text: "WORLDPAY / GENIUS  |  INTERNAL DISCUSSION DRAFT", font: "Arial", size: 16, color: "6B7280" })],
        border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "D9E2F3", space: 5 } },
        spacing: { after: 80 },
      })] }),
    },
    footers: {
      default: new Footer({ children: [new Paragraph({
        children: [
          new TextRun({ text: "Todd Vahlsing  |  August 10, 2026", font: "Arial", size: 16, color: "6B7280" }),
          new TextRun("\t"),
          new TextRun({ text: "Page ", font: "Arial", size: 16, color: "6B7280" }),
          new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 16, color: "6B7280" }),
        ],
        tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
      })] }),
    },
    children: [...(memoOnly ? [] : emailChildren), ...memoChildren],
  }],
});

Packer.toBuffer(doc).then((buffer) => fs.writeFileSync(outputPath, buffer));
