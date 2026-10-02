# Genius POS Product Training — 2026-08-14

**Source:** Just Press Record capture `cap-584c7abb48b38d66`  
**Meeting time:** 2026-08-14 09:03 America/Chicago  
**Evidence:** locally transcribed audio, 14,378 words  
**Confidence:** high for demonstrated functions; medium where speakers described roadmap, integrations, or sales positioning without documentation

## Executive readout

Genius Enterprise POS is a cloud-managed, multi-OS restaurant platform with centralized configuration, menu and pricing control, POS/KDS provisioning, enterprise deployment controls, and built-in operational reporting. The strongest differentiated story is flexibility: brands can preserve or mix supported hardware, manage configuration centrally, deploy multiple devices in parallel, and pair POS, KDS, payments, hardware, and services through one provider.

The training also exposed important qualification boundaries. Kiosk is Windows-based today; some enterprise/table-service feature gaps are still being closed; alerts and third-party behavior can depend on external APIs or polling; robust inventory and labor scheduling may require Genius Back Office or a third party; and the combined hardware/software/payments launch and staging motion is still operationally complex.

## Demonstrated capabilities

- **Provisioning:** A new Android device logs into the Genius portal context, selects company, site, POS or KDS role, and a unique terminal. Initial cloud sync was described as generally completing within five minutes.
- **Device roles and recovery:** A device can switch terminal identity within the same site, allowing a front-counter terminal to replace a failed drive-through terminal. Moving a device to another site requires reprovisioning.
- **Parallel deployment:** Multiple devices can be provisioned concurrently because configuration is pulled from the cloud rather than an on-premise server.
- **Device control:** Android devices can use SOTI kiosk mode and automatically launch the Genius application, limiting staff access to device settings and unrelated applications.
- **Multi-OS POS posture:** The discussion described Windows, Android, and iOS use within a mixed estate, including iPads for line busting and Android for POS or KDS. Confirm exact certified-device and feature parity before customer claims.
- **KDS:** POS and KDS share the application/provisioning model. KDS devices can switch station roles within a site; kitchen schemes control routing and screen layouts. Future orders can be held and released to KDS based on configurable preparation lead time.
- **Enterprise release management:** Enterprise customers can pin versions, test releases in a lab/test site, schedule deployments, monitor package state, and keep POS/KDS versions aligned.
- **Central data management:** The portal manages products, modifiers, discounts, roles, terminal schemes, pricing, channel-specific price rules, and reusable site templates.
- **Menu/channel management:** Genius can act as the central menu and pricing source for integrated third-party channels. 86 notifications are available, but the receiving third party must consume the update. Digital-menu-board behavior depends on the quality and structure of the POS/DMB integration.
- **Reporting:** Built-in reports include sales detail, discounts, refunds, transaction/order lookup, comparative reporting, enterprise sales summary, sales-and-labor views, drill-through analysis, saved views, and CSV/PDF/Excel export.
- **Payments and form factors:** The team discussed countertop hardware with embedded payments and handheld/all-in-one order-and-pay devices. Exact gateway, processor, staging, and certification combinations require validation.
- **Back office:** Time punches, labor data, and labor cost reporting are available. More robust scheduling, inventory, costing, transfers, and vendor workflows may use Genius Back Office or third-party products.

## Product and selling constraints

- Kiosk is Windows-based today; the Android direction was discussed as an active product-path decision.
- The team said the restaurant product currently fits table service better than some enterprise use cases, while enterprise gaps are being closed.
- Real-time device status is visible in the portal, but native email/text offline alerts were not demonstrated.
- Some digital-menu-board and channel behaviors depend on integration-specific mappings, APIs, polling, and third-party execution.
- The combined hardware, software, payment gateway, processing, device staging, and sales-motion ownership model was described as challenging and still being clarified.
- Do not imply feature parity, universal hardware support, universal payment-gateway support, or a completed AI-reporting capability without product confirmation.

## Competitive implications

1. **Lead with hardware preservation and deployment flexibility.** The ability to reuse supported legacy hardware or mix form factors reduces capital pressure and migration friction.
2. **Use cloud provisioning and terminal failover as operational proof.** These are tangible resilience and rollout benefits for enterprise operators.
3. **Position centralized menu/channel control carefully.** It is valuable, but the outcome is only as strong as each integration and third party's update behavior.
4. **Bundle accountability is both an advantage and an execution risk.** Genius/Global Payments can own hardware, software, payments, and services, but the internal responsibility map must be clear before making a customer promise.
5. **Avoid overclaiming against PAR, Qu, Toast, Oracle, or NCR.** This session provided useful positioning hypotheses, not a verified feature-by-feature competitive matrix.

## Follow-up questions

- Obtain the shared reporting materials once available.
- Confirm certified hardware and feature parity by Windows, Android, and iOS.
- Confirm the kiosk roadmap and whether/when Android support is planned.
- Document supported payment gateways, processors, embedded-payment devices, staging ownership, and certification dependencies.
- Document offline transaction behavior, resilience architecture, and alerting options.
- Confirm enterprise feature-gap roadmap and the definition of table-service versus enterprise readiness.
- Confirm AI-reporting scope and timing; the session indicated it would be discussed at the Genius User Conference.
- Build a validated comparison matrix only after product owners confirm claims against PAR, Qu, Toast, Oracle, and NCR.

## RB disposition

- Treat this as **product intelligence**, not relationship intelligence.
- Suppress the generic triage engine's person-name extraction from transcript filler.
- Use this note to support Ryan's McDonald's product-map review, FS/TEC preparation, account Blue Sheets, and future Genius discovery conversations.
