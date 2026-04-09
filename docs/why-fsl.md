# RentMate's License, Explained

**TL;DR:** RentMate's source code is public on GitHub. If you manage your own properties, you can run it for free. If you build integrations on top of it, you can do that too. Two years after we ship any given release, that release automatically becomes Apache 2.0 — fully open source, forever. The one thing you can't do is take our code and launch a competing property management SaaS.

That's the whole deal. The rest of this page is detail.

---

## Can I use RentMate?

**You manage properties (your own or under management) and want to run RentMate for your operations.**
Yes, free, no license fee, as long as your company's annual revenue is under $10M. Self-host it, modify it, integrate it with your other tools. Above $10M, talk to us about a commercial license — pricing is straightforward and we're not trying to make this painful.

**You're building software that integrates with RentMate** — a tenant screening service, an accounting connector, a smart-lock integration.
Yes. Building on top of RentMate's APIs or shipping a module that extends it is fine. You're adding value, not substituting for us.

**You want to launch a property management SaaS built on RentMate's code.**
Not under the [FSL](https://fsl.software/). This is the one use case the license is designed to prevent — competing hosted services are what would make the open development model unsustainable for us. If you have a use case that's close to this line, talk to us about a commercial license.

**You're somewhere in the gray area** — a consultancy deploying RentMate for clients, a PropTech startup using parts of it, a large property manager whose sister company is in software.
Email us. We'd rather have a five-minute conversation than have you guess wrong.

> *This page describes our intent and the spirit of the license. It is not legal advice. The [LICENSE file](#) in the repository is the authoritative document, and you should consult your own counsel for your specific situation.*

---

## The Apache 2.0 conversion is the most important thing on this page

Every release of RentMate automatically converts to Apache 2.0 — a standard, permissive open source license — exactly two years after that release ships. This is written into the license itself. It is not a promise we can revoke.

What this means in practice:

If RentMate the company is acquired and the new owners change direction, the code you depend on becomes fully open source on a rolling schedule. If we go bankrupt, same thing. If we just stop being good at our jobs, the community can fork the two-year-old version and keep going. Your investment in learning, deploying, and customizing RentMate is protected by a mechanism that doesn't depend on us continuing to be trustworthy.

We think this is a stronger guarantee than most "open source" companies actually offer, because most of them depend on the company's ongoing goodwill. Ours is mechanical.

---

## License comparison

| | Source visible | Self-host for own use | Use to launch competing SaaS | Eventually becomes open source |
|---|---|---|---|---|
| MIT / Apache 2.0 | Yes | Yes | Yes | Already is |
| AGPL-3.0 | Yes | Yes (modifications must be published) | Yes (modifications must be published) | No |
| SSPL | Yes | Yes, with conditions on related services | No | No |
| BSL (Business Source License) | Yes | Yes | No | Yes, after a change date set by the licensor |
| **[FSL](https://fsl.software/) (RentMate)** | **Yes** | **Yes** | **No** | **Yes, two years after each release** |

This table describes default terms. Several of these licenses (including ours) allow custom commercial arrangements outside the default.

---

## Why we chose [FSL](https://fsl.software/)

We considered four options: a permissive license (MIT/Apache), AGPL, a proprietary closed-source model, and a source-available license like [FSL](https://fsl.software/) or BSL.

**Permissive licenses** would let an established competitor — Buildium, AppFolio, or a well-funded PropTech startup — take RentMate, host it as a managed service, and undercut the company that funds its development. The same pattern has played out repeatedly in adjacent markets: Elastic, MongoDB, Redis, and HashiCorp all moved away from permissive licenses after watching competitors build businesses on top of their code without contributing back. We'd like to still exist in five years.

**AGPL** prevents a competitor from keeping their modifications private, but it doesn't prevent them from running a competing service — they just have to publish their changes. More importantly, AGPL's network copyleft provision is broad enough that many enterprise legal teams prohibit AGPL software entirely, which would cut us off from a significant portion of our potential customer base. We didn't want to choose a license that locks out the customers we're trying to serve.

**Closed source** would solve the business problem but give up everything we believe is valuable about transparency: the ability to audit how your data is handled, to verify our security claims, to learn from the code, and to fork it if we disappear.

**[FSL](https://fsl.software/)** is the only option that preserves source transparency, allows self-hosting, guarantees eventual open-sourcing, and protects the business that funds development. The tradeoff is that for two years after each release, you can't use that code to compete with us. We think that's a fair price.

---

## For contributors

If you contribute code to RentMate, your contribution is licensed under [FSL](https://fsl.software/) — the same terms as the rest of the codebase — and converts to Apache 2.0 on the same two-year schedule. You retain copyright. We don't ask you to sign a CLA or assign anything to us.

This means RentMate (the company) uses your contribution in our hosted service under the same [FSL](https://fsl.software/) terms that apply to everyone else. We think that's the fair version of this deal: you get a better product and source transparency, we get the runway to keep building, and nobody has to sign paperwork to contribute.

---

## FAQ

**What counts as "competing"?** Offering a hosted property management service that substitutes for RentMate's hosted service. Building tools that integrate with, extend, or complement RentMate is not competing. When in doubt, ask us.

**What does the $10M revenue threshold apply to?** Total annual revenue of the entity using RentMate, including affiliates under common control. We chose total revenue rather than RentMate-attributable revenue because it's verifiable and unambiguous.

**What if RentMate gets acquired?** Existing releases continue their march toward Apache 2.0 on schedule — the new owner cannot stop the conversion. New releases under new ownership are governed by whatever license the new owner chooses, but everything shipped before the acquisition is on the conversion clock and that clock cannot be reset.

**What if RentMate goes out of business?** Same answer. The conversion is mechanical and survives the company.

**Can I get a commercial license that removes the competing-use restriction?** Yes, talk to us. We sell commercial licenses to companies whose use case doesn't fit the default terms.

**Is [FSL](https://fsl.software/) OSI-approved?** No. [FSL](https://fsl.software/) is source-available, not OSI-certified open source. The Apache 2.0 conversion produces OSI-approved open source on the two-year schedule, but the current version of any release is source-available during its first two years.

**Who else uses FSL?** [Sentry created FSL](https://fsl.software/) and uses it for their own products. The broader pattern of source-available licensing — companies moving from permissive licenses to something that protects against SaaS arbitrage — includes HashiCorp (BSL), Elastic (Elastic License), MongoDB (SSPL), and Redis (RSAL/SSPL). FSL is one of the cleaner expressions of this pattern because of the guaranteed Apache 2.0 conversion.

---

## A note on this page

If something here is unclear, contradictory, or doesn't match what you find when you read the actual LICENSE file, that's a bug — please [file an issue](#) or email us. We'd rather fix the explanation than have anyone adopt RentMate based on a misunderstanding.