# ROOKHIDE Hackathon Presentation Template




---

## SLIDE 1: TITLE

**Headline:** Hide Any File Inside a Valid Chess Game

**Subtitle:** Private file sharing that looks like ordinary gameplay

**Visuals:**
- Full-width chess board image
- File and lock icon overlay
- ROOKHIDE logo

**Speaker Notes:**
"ROOKHIDE hides a file inside a chess game that still looks normal and can be replayed anywhere."

---

## SLIDE 2: REAL-WORLD PROBLEM

**Headline:** People Share Sensitive Files Every Day

**Bullets:**
- Offer letters, IDs, contracts, and invoices move through WhatsApp, email, and cloud links
- Encrypted files look suspicious to non-technical users
- Current privacy tools are often hard to explain and awkward to use

**Visuals:**
- WhatsApp/email/file-sharing screenshots
- Red-flag style "suspicious file" icon
- Everyday examples: student docs, freelancer contracts, HR paperwork

**Speaker Notes:**
"The problem is not just security. It is making private sharing look normal in daily life."

---

## SLIDE 3: SOLUTION

**Headline:** Chess Moves Become the Container

**Bullets:**
- Every legal chess position has multiple valid moves
- We map data bits to move choices
- The output is a standard PGN, so it still looks like a real game
- Optional compression and password protection happen before encoding

**Visuals:**
- File -> compress/encrypt -> moves -> PGN -> file flow
- Chess board with 2-3 legal move options highlighted
- Sample PGN snippet

**Speaker Notes:**
"We are not breaking chess. We are using legal move choices as the data channel."

---

## SLIDE 4: HOW IT WORKS

**Headline:** Step-by-Step Encoding Flow

**Bullets:**
1. User uploads any file
2. File is compressed and optionally encrypted
3. Bits are encoded into legal move selections
4. A valid chess game is generated
5. Decoding replays the same logic to recover the original file

**Visuals:**
- Large left-to-right process diagram
- Upload screen screenshot
- Replay board / visualizer screenshot

**Speaker Notes:**
"The process is deterministic, so the same PGN always decodes back to the same file."

---

## SLIDE 5: FEASIBILITY

**Headline:** Built for a Hackathon, Ready for Real Use

**Bullets:**
- Technical feasibility: Flask handles the web app and Rust handles the heavy move generation
- Operational feasibility: works in browser, no special client needed
- Security feasibility: optional encryption, hidden metadata, and deterministic recovery
- Performance: around 1 MB in 3.6 seconds on Apple Silicon

**Visuals:**
- Simple architecture diagram
- Speed bar chart
- Rust + Python stack graphic

**Speaker Notes:**
"This is feasible because the app is already split cleanly into UI, orchestration, and engine layers."

---

## SLIDE 6: BUSINESS MODEL

**Headline:** Simple INR Pricing and Cost Model

**Bullets:**
- Free tier: basic file encoding for demos and users
- Pro plan: ₹299/month
- Team plan: ₹1,499/month
- Enterprise plan: ₹14,999/month or custom pricing

**Cost Side:**
- Hosting, storage, and bandwidth: about ₹15,000/month
- Maintenance and support: about ₹20,000/month
- Marketing and demo ops: about ₹10,000/month
- Estimated monthly operating cost: about ₹45,000/month

**Revenue Example:**
- 100 Pro users = ₹29,900/month
- 20 Team users = ₹29,980/month
- 5 Enterprise users = ₹74,995/month
- Example MRR = ₹1,34,875/month

**Visuals:**
- Pricing card layout
- Small cost vs revenue chart
- Monthly recurring revenue bar

**Speaker Notes:**
"The model is straightforward: low-cost software, recurring subscriptions, and enterprise upsell."

---

## SLIDE 7: MARKET OPPORTUNITY

**Headline:** Target Users and Market Size

**Bullets:**
- Target audience: students, freelancers, HR teams, law firms, fintech teams, and enterprises
- India cybersecurity market is roughly ₹25,000-30,000 crore annually, based on industry estimates
- Global encryption software and privacy-tech revenue is around ₹1.2-1.3 lakh crore annually
- Existing security and encryption products already prove that people pay for privacy

**Visuals:**
- TAM/SAM/SOM style chart
- India map highlight
- Audience icons for students, SMBs, and enterprises

**Speaker Notes:**
"This is a real market because security and privacy tools already generate large recurring revenue."

---

## SLIDE 8: PENETRATION TESTING AND RESILIENCE

**Headline:** Penetration Testing: Existing Methods vs ROOKHIDE

**Bullets:**
- Traditional encrypted files are easy to flag using entropy and signature checks
- Common image/audio steganography is vulnerable to statistical steganalysis and ML classifiers
- ROOKHIDE output remains valid PGN, so baseline file-level checks classify it as ordinary chess notation
- Internal red-team tests focus on detection, tampering, replay consistency, and decode integrity

**Testing Comparison (for slide table):**
- AES/RSA containers: high detectability, low deniability
- LSB image stego: medium-high detectability under modern steganalysis
- ROOKHIDE PGN encoding: low detectability in format-based scanning, with deterministic recovery

**Visuals:**
- 3-column comparison matrix (Existing Encryption / Stego / ROOKHIDE)
- Attack-path diagram (detect -> tamper -> decode)
- Small test-result heatmap or pass/fail grid

**Speaker Notes:**
"We benchmarked ROOKHIDE against common detection paths. Conventional encrypted artifacts are easy to flag. ROOKHIDE behaves like valid chess notation, which improves stealth at the file-format layer while preserving recovery integrity."

---

## SLIDE 9: TEAM

**Headline:** 4-Person Execution Team

**Bullets:**
- Anurag - Product direction and backend orchestration
- Kartik - Testing, documentation, and deployment support
- Pranay - Frontend, visualizer, and user experience
- Rishu - Rust engine and performance optimization 

**Visuals:**
- Team photo or 4-card headshot layout
- Contribution icons per member
- Small "Meet Our Team" screenshot from the app

**Speaker Notes:**
"Each person owns a clear part of the product, so the build stays focused and fast."

---

## SLIDE 10: CLOSE

**Headline:** ROOKHIDE ♟️🔐

**Bullets:**
- Hide anything
- Look normal
- Share privately
- Decode reliably

**Visuals:**
- Logo
- QR code
- Contact or demo link

**Speaker Notes:**
"ROOKHIDE turns private file sharing into something that looks ordinary."

---

# QUICK PRESENTER NOTES

**Hook:** What if a private file just looked like a normal chess game?

**Problem:** Sensitive files are shared every day, but encrypted files often look suspicious.

**Solution:** Encode data through legal chess moves and decode it by replaying the same game.

**Feasibility:** Fast Rust core, simple Flask app, browser-based usage, and optional encryption.

**Business:** Low operating cost, recurring INR subscriptions, and enterprise pricing.

**Market:** Students, freelancers, SMBs, law firms, fintech, and enterprises already pay for privacy tech.
