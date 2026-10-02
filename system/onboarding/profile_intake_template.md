# RB Profile Intake Template

Fill in this template to create your RB user profile. The profile bootstrap
CLI (`profile_bootstrap.py`) can guide you through these questions interactively,
or you can fill this in directly and run `profile_bootstrap.py --from-template`.

Instructions: replace `null` with your answers. Lists use the `- item` format.
Omit any field you don't want to answer; RB will ask only consequential follow-ups.

---

## 1. Identity

```yaml
# Your full name
name: null

# The name you prefer to go by
preferred_name: null

# Current role title
current_role: null

# Current employer or company name (use "Independent" if self-employed)
current_company: null

# Location (city, state)
location: null

# LinkedIn URL (optional)
linkedin_url: null

# Email address
email: null
```

---

## 2. Career / Business Context

```yaml
# Your primary career context — pick one:
# employed | consulting | job_search | founder | operator | advisor | investor | other
career_context: null

# If employed or consulting: industry and sub-industry
industry: null
sub_industry: null

# If in a job search: brief description of what you're looking for
job_search_context: null
```

---

## 3. Relationship Goals

What do you want RB to help you accomplish? List all that apply.

```yaml
# job_search | sales_pipeline | consulting_advisory | fundraising |
# hiring_recruiting | partnerships | community_building | personal_stewardship
relationship_goals:
  - null
```

---

## 4. Core Capabilities

What can you personally deliver? Be specific — these drive opportunity fit scoring.

```yaml
# Examples:
#   - enterprise_saas_sales
#   - restaurant_operations_consulting
#   - cto_fractional_technology_leadership
capabilities:
  - null
```

---

## 5. Product / Service Capabilities

If you have a business, practice, or consulting offering, what does it deliver?

```yaml
# Examples:
#   - gtm_strategy_for_early_stage_saas
#   - fractional_cfo_services
#   - restaurant_technology_advisory
product_service_capabilities:
  - null
```

---

## 6. Industries and Adjacent Markets

```yaml
# Primary industries you operate in or target
target_industries:
  - null

# Adjacent industries — secondary fit, worth monitoring
adjacent_industries:
  - null
```

---

## 7. Target Customers and Target Employers

```yaml
# Who are you trying to sell to or advise?
target_customers:
  - null

# What kinds of companies would you consider working for?
target_employers:
  - null
```

---

## 8. Pain You Can Credibly Solve

What problems can you help organizations fix? Be honest — weak claims waste everyone's time.

```yaml
# Examples:
#   - enterprise_sales_stall
#   - franchise_adoption_resistance
#   - gtm_execution_failure
#   - vendor_gap
#   - leadership_gap
credible_pain_types:
  - null
```

---

## 9. Opportunity Types You Want Surfaced

```yaml
# sales | job | consulting | partnership | intro | content | research
preferred_opportunity_types:
  - null

# Things you do NOT want surfaced
no_go_categories:
  - null
```

---

## 10. Communication Preferences

```yaml
# Your tone style — plain English description
tone_style: null

# Words or phrases you never want in drafts
prohibited_language:
  - null

# Preferred response mode: expert | intermediate | beginner
response_mode: expert

# Verbosity: concise | normal | detailed
verbosity: concise
```

---

## 11. Relationship and Intro Boundaries

```yaml
# conservative | balanced | aggressive
intro_philosophy: balanced

# Hard rules for introductions
intro_boundaries:
  - null

# Engagement models you will not accept
engagement_no_gos:
  - null
```

---

## 12. Privacy and Conflict Constraints

```yaml
# Any employers or clients whose involvement should stay confidential?
confidential_relationships:
  - null

# Any competitive conflicts to be aware of?
conflict_constraints:
  - null
```

---

## 13. Constraints

```yaml
# Geographic preference: open | us_only | region_name | specific_city
geographic: open

# Travel availability: open | limited | none
travel: open

# Work style preference: remote | hybrid | in_person | no_preference
work_style: no_preference
```

---

## Done

Save this file and run:

```bash
python3 system/onboarding/profile_bootstrap.py --from-template path/to/this/file.md --profile-id your_name
```

RB will parse your answers, create the canonical profile directory, and confirm
what it wrote before touching anything.
