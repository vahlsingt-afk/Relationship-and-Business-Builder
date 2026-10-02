from copy import copy
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "output/worldpay/Worldpay_Master_Account_Plan_2026-09-30.xlsx"
OUTPUT = ROOT / "output/worldpay/Worldpay_Master_Account_Plan_2026-09-30_DEEPENED.xlsx"


# Public counts are deliberately conservative and carry a scope label. Blank counts on
# legal entities are intentional: a brand-wide number would misstate the WP merchant.
PUBLIC_COUNTS = {
    "Coffee Bean & Tea Leaf Ma": (193, "U.S. brand footprint", "High", "https://www.coffeebean.com/store/index.html"),
    "Caribou Coffee #1243": (800, "Global brand footprint; WP row may be one merchant/site", "Medium", "https://www.cariboucoffee.com/about-us/"),
    "Jamba Juice": (720, "U.S. brand footprint", "High", "https://locations.jamba.com/"),
    "Hungry Howies": (500, "Approximate U.S. brand footprint", "High", "https://hungryhowies.com/about-us"),
    "Carvel": (374, "U.S. brand footprint", "High", "https://locations.carvel.com/"),
    "Bruegger'S Enterprises In": (181, "Brand footprint; reported Oct. 2023", "Medium", "https://www.brueggers.com/wp-content/uploads/2024/06/BB-ESG-Report-2023.pdf"),
    "Uno'S": (100, "Global brand footprint", "High", "https://unos.com/about/"),
}


SEGMENTS = {
    "Coffee Bean & Tea Leaf Ma": "Coffee & Beverage",
    "International Coffee & Tea Llc": "Coffee & Beverage",
    "Caribou Coffee #1243": "Coffee & Beverage",
    "Jamba Juice": "Juice / Smoothie",
    "American Blue Ribbon Holdings": "Restaurant Group / Platform",
    "Aurify Brands, Llc": "Restaurant Group / Platform",
    "Peet'S Coffee & Tea": "Coffee & Beverage",
    "Yum Yum Donuts": "Bakery / Dessert",
    "Boston Market": "QSR — Chicken",
    "Carvel": "Dessert / Ice Cream",
    "Sarku Japan": "Fast Casual — Asian",
    "Fazolis": "Fast Casual — Italian",
    "Ruby Tuesday": "Casual Dining",
    "Orange Leaf": "Dessert / Frozen Yogurt",
    "Wow Bao Llc": "Fast Casual — Asian",
    "Bruegger'S Enterprises In": "Bakery / Breakfast",
    "Honey Dew Donuts": "Coffee / Bakery",
    "Logan'S Roadhouse": "Casual Dining — Steakhouse",
    "Famous Dave'S Bbq": "Casual Dining — Barbecue",
    "Friendly Ice Cream #7776": "Family Dining / Dessert",
    "Hwy 55 Burgers Shakes & F": "Fast Casual — Burger",
    "Tijuana Flats": "Fast Casual — Mexican",
    "Shoneys": "Family Dining",
    "Frischs": "Family Dining / Burger",
    "Chuy'S": "Casual Dining — Mexican",
    "Fuddruckers": "Fast Casual — Burger",
    "Gold Star Chili": "QSR — Chili / Sandwich",
    "Bennigan'S": "Casual Dining",
    "La Madeleine": "Fast Casual — Bakery Café",
    "Uno'S": "Casual Dining — Pizza",
    "Luby'S Restaurants": "Cafeteria / Family Dining",
    "Scramblers": "Family Dining / Breakfast",
    "Tom & Chee": "Fast Casual — Sandwich",
    "Ted'S Montana Grill, Inc.": "Casual Dining — Steakhouse",
    "Dibella'S": "Fast Casual — Sandwich",
    "Latrelle'S Galley Lp": "Airport / Contract Foodservice",
    "Houlihan'S Restaurants Inc": "Casual Dining",
    "Norsan Restaurants Inc": "Restaurant Group / Franchisee",
    "Winghouse": "Casual Dining — Sports Bar",
    "Creole Cuisine Restaurant": "Restaurant Group / Platform",
    "La Carreta Group Inc": "Restaurant Group / Franchisee",
    "Jake N Joes": "Casual Dining — Sports Bar",
    "Chateau Restaurant": "Casual Dining — Italian",
    "Cyclone Anaya": "Casual Dining — Mexican",
    "Jeff Ruby": "Fine Dining — Steakhouse",
    "Mark'S Feed Store": "Casual Dining — Barbecue",
    "Cocula Restaurant": "Casual Dining — Mexican",
    "David'S Pizza": "Pizza",
    "Gibsons Llc": "Fine Dining — Steakhouse",
    "Sam Choy'S": "Casual Dining — Hawaiian",
    "Tibby New Orleans Kitchen": "Casual Dining — Cajun / Creole",
    "Siren Retail Corporation": "Coffee & Beverage",
    "Amanda'S Fonda": "Casual Dining — Mexican",
    "Broadway On Deck Llc": "Merchant / Legal Entity",
    "Down The Hatch": "Casual Dining / Bar",
    "Fontana Subway Sandwiches": "Franchisee — Sandwich",
    "Forward Subway": "Franchisee — Sandwich",
    "Jb'S On The Beach": "Casual Dining",
    "Jy5, Inc": "Merchant / Franchisee Entity",
    "Kj Endeavors, Llc": "Merchant / Franchisee Entity",
    "Langhorne, Pa #727, Llc": "Merchant / Franchisee Entity",
    "Logan Dining Llc": "Merchant / Franchisee Entity",
    "Marlton Restaurant, Llc": "Merchant / Franchisee Entity",
    "Michael Nelson Enterprise": "Merchant / Franchisee Entity",
    "Ms Roosevelt Field, Llc": "Mall / Licensed Location",
    "Off The Wagon": "Casual Dining / Bar",
    "Robek S #289": "Franchisee — Juice / Smoothie",
    "Sc 10024, Llc": "Merchant / Franchisee Entity",
    "Subway Tryan": "Franchisee — Sandwich",
    "Ten Star Enterprises #9": "Merchant / Franchisee Entity",
    "The Gin Mill": "Casual Dining / Bar",
    "The Stumble Inn": "Casual Dining / Bar",
    "Avi Foodsystems": "Contract Foodservice",
    "D&C Foods Inc #5": "Merchant / Franchisee Entity",
    "Lunchdrop": "Corporate Catering / Marketplace",
    "Smashburger #1567": "Franchisee — Burger",
    "Bun Two Llc / Cinnabon": "Franchisee — Bakery / Dessert",
}


ENTITY_MARKERS = (" llc", " inc", " corp", "corporation", "enterprise", " #", " lp", "group")


def account_type(name: str) -> str:
    lower = name.lower()
    if name in {"American Blue Ribbon Holdings", "Aurify Brands, Llc", "Creole Cuisine Restaurant"}:
        return "Restaurant group / platform"
    if any(marker in lower for marker in ENTITY_MARKERS) or name in {
        "International Coffee & Tea Llc", "Fontana Subway Sandwiches", "Forward Subway", "Subway Tryan"
    }:
        return "Legal entity / franchisee / site record"
    return "Brand / chain"


def rebuild_table(ws, table_name: str, ref: str, style_name: str = "TableStyleMedium2"):
    if table_name in ws.tables:
        del ws.tables[table_name]
    tab = Table(displayName=table_name, ref=ref)
    tab.tableStyleInfo = TableStyleInfo(name=style_name, showFirstColumn=False, showLastColumn=False,
                                        showRowStripes=True, showColumnStripes=False)
    ws.add_table(tab)


wb = load_workbook(SOURCE)
rp = wb["Ranked Portfolio"]
ao = wb["Added Accounts Overview"]

# Ranked Portfolio: append governance fields for all supplemental rows.
rp_headers = [c.value for c in rp[4]]
new_rp_headers = ["Account Type", "Location Scope", "Location Confidence", "WP Relationship Owner", "Internal Clarification Needed"]
for offset, header in enumerate(new_rp_headers, start=len(rp_headers) + 1):
    cell = rp.cell(4, offset, header)
    cell._style = copy(rp.cell(4, len(rp_headers))._style)
    cell.font = copy(rp.cell(4, len(rp_headers)).font)
    cell.alignment = copy(rp.cell(4, len(rp_headers)).alignment)

name_col = rp_headers.index("Account") + 1
loc_col = rp_headers.index("Locations") + 1
segment_col = rp_headers.index("Sub-segment") + 1
source_col = rp_headers.index("Supplemental Source") + 1
for row in range(35, rp.max_row + 1):
    name = rp.cell(row, name_col).value
    if not name:
        continue
    a_type = account_type(name)
    rp.cell(row, segment_col, SEGMENTS.get(name, rp.cell(row, segment_col).value))
    if name in PUBLIC_COUNTS:
        count, scope, confidence, url = PUBLIC_COUNTS[name]
        rp.cell(row, loc_col, count)
        existing_source = rp.cell(row, source_col).value or ""
        if url not in existing_source:
            rp.cell(row, source_col, f"{existing_source} | {url}".strip(" |"))
    else:
        existing = rp.cell(row, loc_col).value
        if existing:
            scope, confidence = "Brand/system footprint from existing RBB research", "Medium"
        elif a_type == "Brand / chain":
            scope, confidence = "Brand footprint not yet verified", "Needs research"
        else:
            scope, confidence = "WP merchant/entity footprint—not safe to infer from brand", "Needs WP confirmation"
    question = (
        "Who at Worldpay owns this relationship, what merchant IDs/legal entities are in scope, "
        "and is coverage enterprise, franchisee, or a single site?"
    )
    values = [a_type, scope, confidence, "Unconfirmed — ask Worldpay", question]
    for i, value in enumerate(values, start=len(rp_headers) + 1):
        c = rp.cell(row, i, value)
        c._style = copy(rp.cell(row, len(rp_headers))._style)
        c.alignment = Alignment(vertical="top", wrap_text=True)

rp.column_dimensions["AH"].width = 30
rp.column_dimensions["AI"].width = 46
rp.column_dimensions["AJ"].width = 22
rp.column_dimensions["AK"].width = 28
rp.column_dimensions["AL"].width = 62
rebuild_table(rp, "RankedPortfolioTable", f"A4:AL{rp.max_row}")

# Added Accounts Overview mirrors the same governance fields and refreshed facts.
ao_headers = [c.value for c in ao[4]]
new_ao_headers = ["Account Type", "Location Scope", "Location Confidence", "WP Relationship Owner", "Internal Clarification Needed"]
for offset, header in enumerate(new_ao_headers, start=len(ao_headers) + 1):
    c = ao.cell(4, offset, header)
    c._style = copy(ao.cell(4, len(ao_headers))._style)
    c.font = copy(ao.cell(4, len(ao_headers)).font)
    c.alignment = copy(ao.cell(4, len(ao_headers)).alignment)

rp_by_name = {rp.cell(r, name_col).value: r for r in range(35, rp.max_row + 1)}
for row in range(5, ao.max_row + 1):
    name = ao.cell(row, 2).value
    if not name or name not in rp_by_name:
        continue
    rr = rp_by_name[name]
    ao.cell(row, 4, rp.cell(rr, segment_col).value)
    ao.cell(row, 6, rp.cell(rr, loc_col).value)
    if name in PUBLIC_COUNTS:
        url = PUBLIC_COUNTS[name][3]
        old = ao.cell(row, 13).value or ""
        if url not in old:
            ao.cell(row, 13, f"{old} | {url}".strip(" |"))
        ao.cell(row, 8, f"Public footprint refreshed 2026-09-30; distinguish brand footprint from the exact WP merchant/entity scope.")
        ao.cell(row, 12, "Public footprint refreshed; WP ownership open")
    vals = [rp.cell(rr, c).value for c in range(34, 39)]
    for i, value in enumerate(vals, start=len(ao_headers) + 1):
        c = ao.cell(row, i, value)
        c._style = copy(ao.cell(row, len(ao_headers))._style)
        c.alignment = Alignment(vertical="top", wrap_text=True)

for col, width in {"N":30, "O":46, "P":22, "Q":28, "R":62}.items():
    ao.column_dimensions[col].width = width
rebuild_table(ao, "AddedAccountsOverview", f"A4:R{ao.max_row}")

# Make the meeting objective and dashboard explicit about the two separate unknowns.
es = wb["Executive Summary"]
es["A11"] = (
    "The plan now accommodates 115 accounts: the original 30 scored opportunities plus 85 supplemental Worldpay backbook accounts. "
    "The supplemental rows now distinguish brand footprint from the exact WP merchant/entity footprint, use normalized operating segments, "
    "and explicitly flag Worldpay relationship ownership as unconfirmed until the WP team names an owner."
)
es["A22"] = (
    "• For every supplemental row, name the Worldpay relationship owner—even if that person is not the formal RM.\n"
    "• Confirm the merchant/legal entity and whether WP coverage is enterprise, franchisee, or single-site.\n"
    "• Validate location scope before using brand-wide counts for legal entities or numbered stores.\n"
    "• Select 5–10 accounts for full discovery; keep unresolved merchant records in qualification.\n"
    "• Confirm how attrition and dyssynergy flags should affect pursuit."
)

dash = wb["Dashboard"]
dash["B7"] = "WP owner confirmed"
dash["B8"] = '=COUNTIFS(\'Added Accounts Overview\'!Q5:Q200,"<>Unconfirmed — ask Worldpay",\'Added Accounts Overview\'!Q5:Q200,"<>")'
dash["E7"] = "Public footprints refreshed"
dash["E8"] = '=COUNTIF(\'Added Accounts Overview\'!L5:L200,"Public footprint refreshed*")'
dash["H7"] = "Needs WP owner"
dash["H8"] = '=COUNTIF(\'Added Accounts Overview\'!Q5:Q200,"Unconfirmed — ask Worldpay")'
dash["K7"] = "Entity/site records"
dash["K8"] = '=COUNTIF(\'Added Accounts Overview\'!N5:N200,"Legal entity*")'
dash["B23"] = (
    "Brand footprint and WP merchant footprint are separate fields. No supplemental account should be treated as owned until Worldpay names "
    "the relationship owner and confirms enterprise vs. franchisee/site scope."
)

for ws in (rp, ao, es, dash):
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                cell.font = copy(cell.font)
                cell.font = Font(name="Arial", size=cell.font.sz or 10, bold=cell.font.bold,
                                 italic=cell.font.italic, color=cell.font.color)

wb.calculation.fullCalcOnLoad = True
wb.calculation.forceFullCalc = True
wb.calculation.calcMode = "auto"
wb.save(OUTPUT)
print(OUTPUT)
