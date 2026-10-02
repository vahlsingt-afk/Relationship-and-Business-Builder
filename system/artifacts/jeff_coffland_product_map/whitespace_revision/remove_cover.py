from docx import Document

SRC = "system/artifacts/jeff_coffland_product_map/Genius_Product_Map_McDonalds.docx"
OUT = SRC
d = Document(SRC)
body = d._element.body

# The first product section starts with the page-break paragraph immediately
# before "PRODUCT MAP 1 OF 5". Remove the cover and that leading break so the
# document opens directly on the product map.
start = None
for i, el in enumerate(body):
    text = " ".join(el.xpath('.//w:t/text()'))
    if "PRODUCT MAP 1 OF 5" in text:
        start = i
        break
if start is None:
    raise RuntimeError("Could not locate first product-map section")
keep_from = start
if start > 0 and body[start - 1].xpath('.//w:br[@w:type="page"]'):
    keep_from = start - 1
for el in list(body)[:keep_from]:
    body.remove(el)
if body[0].xpath('.//w:br[@w:type="page"]'):
    body.remove(body[0])

# Renumber the four remaining product-map pages.
for p in d.paragraphs:
    if p.text.strip().startswith("PRODUCT MAP "):
        updated = p.text.replace(" OF 5", " OF 4")
        for r in p.runs:
            if r.text:
                r.text = updated
                for extra in p.runs[1:]:
                    extra.text = ""
                break

d.core_properties.title = "Genius Product Map — McDonald’s"
d.save(OUT)
print(OUT)
