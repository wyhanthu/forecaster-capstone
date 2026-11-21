# Forecaster screening report (2026-11-01)

Synthetic demo universe: all companies and numbers are fictional.
Funnel outcome: exclude: 2, include: 9, insufficient_data: 1, prescreen_fail: 1, uncertain: 1

## Shortlist

| # | Ticker | Sector | Score | Confidence | Interp. | Auto-release |
|---|---|---|---|---|---|---|
| 1 | CYAN | Technology | 1.00 | high | stable | yes |
| 2 | NOVA | Technology | 1.00 | high | stable | review |
| 3 | ALPH | Industrials | 0.71 | high | stable | review |
| 4 | EMBR | Energy | 0.64 | high | setback | review |
| 5 | BRCK | Consumer Staples | 0.62 | high | stable | yes |
| 6 | LUMN | Utilities | 0.45 | high | stable | yes |

Skipped by portfolio guardrails: JADE (sponsor cap (Cedar Partners: max 2)); MRDN (sector flagged industry-wide weakness via DRFT (max 1)); DRFT (sector flagged industry-wide weakness via DRFT (max 1))

## Company memos

### CYAN - Cyan Software Ltd. (Technology)
Score 1.00 | confidence high | interpretation: stable

**Filing facts (verbatim)**
- Total debt outstanding was $760.0 million as of 2026Q3. [1]
- Interest coverage, measured as EBITDA divided by interest expense, was 34.0x for the quarter ended 2026Q3. [2]
- Scheduled maturities: 2026 $50.0 million, 2027 $60.0 million, 2028 $100.0 million, 2029 $200.0 million, 2030 $350.0 million. [1]
- We had cash and cash equivalents of $900.0 million as of 2026Q3. [2]
- The credit agreement requires net leverage not to exceed 4.5x; actual net leverage was 0.6x as of 2026Q3. [3]
- Operating lease obligations due within the next two years total $38.0 million. [4]
- Purchase and other commitments due within the next two years total $22.8 million. [5]
- Floating-rate debt represented 30% of total debt after hedging. [6]
- Management evaluated events through the filing date and identified no material subsequent events. [7]
- The condensed consolidated financial statements were prepared in accordance with U.S. GAAP. [8]

**Computed ratios**
- Total debt / LTM EBITDA = 0.58x [9][10][11][12][13]
- EBITDA / interest expense (quarter) = 34.00x [13][14]
- Current assets / current liabilities = 2.14 [15][16]
- (Cash + 2 x max(FCF,0)) / debt maturing 2026-2028 = 12.00x (projection: flat FCF) [17][18][19][20][21][22]
- (Cash + revolver + 2 x max(FCF,0)) / (maturities 2026-2028 + leases + commitments + litigation + 2 x rate shock) = 10.78x (projection: flat FCF) [17][18][19][20][21][22][23][24][25][26][27][5][3][6][4]
- (Covenant limit - actual) / covenant limit = 0.87 [28][29][3]

*Judgment: the cited filings show a stable credit profile with no multi-period deterioration.* (model judgment, not a filing fact)

[1] 10-Q filed 2026-10-28, Debt & maturity footnote (`cyan-10-q-2026-10-28-debt_footnote`)
[2] 10-Q filed 2026-10-28, Item 7 / MD&A (Liquidity) (`cyan-10-q-2026-10-28-item7`)
[3] 10-Q filed 2026-10-28, Note: Credit facilities & covenants (`cyan-10-q-2026-10-28-note_credit`)
[4] 10-Q filed 2026-10-28, Note: Leases & off-balance-sheet arrangements (`cyan-10-q-2026-10-28-note_leases`)
[5] 10-Q filed 2026-10-28, Note: Commitments & contingencies (`cyan-10-q-2026-10-28-note_commitments`)
[6] 10-Q filed 2026-10-28, Note: Derivatives & interest-rate exposure (`cyan-10-q-2026-10-28-note_derivatives`)
[7] 10-Q filed 2026-10-28, Note: Subsequent events (`cyan-10-q-2026-10-28-note_subsequent`)
[8] 10-Q filed 2026-10-28, Note: Basis of presentation (going concern / restatement) (`cyan-10-q-2026-10-28-note_basis`)
[9] XBRL fact `xbrl:0001000003:TotalDebt:2026Q3`
[10] XBRL fact `xbrl:0001000003:EBITDA:2025Q4`
[11] XBRL fact `xbrl:0001000003:EBITDA:2026Q1`
[12] XBRL fact `xbrl:0001000003:EBITDA:2026Q2`
[13] XBRL fact `xbrl:0001000003:EBITDA:2026Q3`
[14] XBRL fact `xbrl:0001000003:InterestExpense:2026Q3`
[15] XBRL fact `xbrl:0001000003:CurrentAssets:2026Q3`
[16] XBRL fact `xbrl:0001000003:CurrentLiabilities:2026Q3`
[17] XBRL fact `xbrl:0001000003:Cash:2026Q3`
[18] XBRL fact `xbrl:0001000003:OperatingCashFlow:FY2025`
[19] XBRL fact `xbrl:0001000003:Capex:FY2025`
[20] XBRL fact `xbrl:0001000003:DebtMaturity:2026`
[21] XBRL fact `xbrl:0001000003:DebtMaturity:2027`
[22] XBRL fact `xbrl:0001000003:DebtMaturity:2028`
[23] `note:0001000003:RevolverAvailability:2026Q3`
[24] `note:0001000003:LeaseObligations2Y:2026Q3`
[25] `note:0001000003:Commitments2Y:2026Q3`
[26] `note:0001000003:LitigationPossibleLoss:2026Q3`
[27] `note:0001000003:RateShockInterest:2026Q3`
[28] `note:0001000003:CovenantMax:2026Q3`
[29] `note:0001000003:CovenantActual:2026Q3`

### NOVA - Nova Semiconductor Inc. (Technology)
Score 1.00 | confidence high | interpretation: stable | maturity wall 2028

**Filing facts (verbatim)**
- Total debt outstanding was $975.0 million as of 2026Q3. [1]
- Interest coverage, measured as EBITDA divided by interest expense, was 18.5x for the quarter ended 2026Q3. [2]
- Scheduled maturities: 2026 $60.0 million, 2027 $90.0 million, 2028 $160.0 million, 2029 $300.0 million, 2030 $365.0 million. [1]
- We had cash and cash equivalents of $700.0 million as of 2026Q3. [2]
- The credit agreement requires net leverage not to exceed 4.5x; actual net leverage was 0.9x as of 2026Q3. [3]
- Operating lease obligations due within the next two years total $48.8 million. [4]
- Purchase and other commitments due within the next two years total $29.2 million. [5]
- Floating-rate debt represented 30% of total debt after hedging. [6]
- Subsequent to quarter end, the Company repaid $200.0 million of outstanding term loans. [7]
- The condensed consolidated financial statements were prepared in accordance with U.S. GAAP. [8]
- Subsequent to quarter end, the Company repaid $200.0 million of outstanding term loans. [7]

**Computed ratios**
- Total debt / LTM EBITDA = 0.92x [9][10][11][12][13]
- EBITDA / interest expense (quarter) = 18.53x [13][14]
- Current assets / current liabilities = 2.00 [15][16]
- (Cash + 2 x max(FCF,0)) / debt maturing 2026-2028 = 5.68x (projection: flat FCF) [17][18][19][20][21][22]
- (Cash + revolver + 2 x max(FCF,0)) / (maturities 2026-2028 + leases + commitments + litigation + 2 x rate shock) = 5.36x (projection: flat FCF) [17][18][19][20][21][22][23][24][25][26][27][5][3][6][4]
- (Covenant limit - actual) / covenant limit = 0.80 [28][29][3]

*Judgment: the cited filings show a stable credit profile with no multi-period deterioration.* (model judgment, not a filing fact)

**Needs analyst review:** subsequent event in the notes: “Subsequent to quarter end, the Company repaid $200.0 million of outstanding term loans.” [nova-10-q-2026-10-28-note_subsequent]

[1] 10-Q filed 2026-10-28, Debt & maturity footnote (`nova-10-q-2026-10-28-debt_footnote`)
[2] 10-Q filed 2026-10-28, Item 7 / MD&A (Liquidity) (`nova-10-q-2026-10-28-item7`)
[3] 10-Q filed 2026-10-28, Note: Credit facilities & covenants (`nova-10-q-2026-10-28-note_credit`)
[4] 10-Q filed 2026-10-28, Note: Leases & off-balance-sheet arrangements (`nova-10-q-2026-10-28-note_leases`)
[5] 10-Q filed 2026-10-28, Note: Commitments & contingencies (`nova-10-q-2026-10-28-note_commitments`)
[6] 10-Q filed 2026-10-28, Note: Derivatives & interest-rate exposure (`nova-10-q-2026-10-28-note_derivatives`)
[7] 10-Q filed 2026-10-28, Note: Subsequent events (`nova-10-q-2026-10-28-note_subsequent`)
[8] 10-Q filed 2026-10-28, Note: Basis of presentation (going concern / restatement) (`nova-10-q-2026-10-28-note_basis`)
[9] XBRL fact `xbrl:0001000014:TotalDebt:2026Q3`
[10] XBRL fact `xbrl:0001000014:EBITDA:2025Q4`
[11] XBRL fact `xbrl:0001000014:EBITDA:2026Q1`
[12] XBRL fact `xbrl:0001000014:EBITDA:2026Q2`
[13] XBRL fact `xbrl:0001000014:EBITDA:2026Q3`
[14] XBRL fact `xbrl:0001000014:InterestExpense:2026Q3`
[15] XBRL fact `xbrl:0001000014:CurrentAssets:2026Q3`
[16] XBRL fact `xbrl:0001000014:CurrentLiabilities:2026Q3`
[17] XBRL fact `xbrl:0001000014:Cash:2026Q3`
[18] XBRL fact `xbrl:0001000014:OperatingCashFlow:FY2025`
[19] XBRL fact `xbrl:0001000014:Capex:FY2025`
[20] XBRL fact `xbrl:0001000014:DebtMaturity:2026`
[21] XBRL fact `xbrl:0001000014:DebtMaturity:2027`
[22] XBRL fact `xbrl:0001000014:DebtMaturity:2028`
[23] `note:0001000014:RevolverAvailability:2026Q3`
[24] `note:0001000014:LeaseObligations2Y:2026Q3`
[25] `note:0001000014:Commitments2Y:2026Q3`
[26] `note:0001000014:LitigationPossibleLoss:2026Q3`
[27] `note:0001000014:RateShockInterest:2026Q3`
[28] `note:0001000014:CovenantMax:2026Q3`
[29] `note:0001000014:CovenantActual:2026Q3`

### ALPH - Alpine Components Inc. (Industrials)
Score 0.71 | confidence high | interpretation: stable

**Filing facts (verbatim)**
- Total debt outstanding was $1,420.0 million as of 2026Q3. [1]
- Interest coverage, measured as EBITDA divided by interest expense, was 7.3x for the quarter ended 2026Q3. [2]
- Scheduled maturities: 2026 $100.0 million, 2027 $150.0 million, 2028 $200.0 million, 2029 $300.0 million, 2030 $700.0 million. [1]
- We had cash and cash equivalents of $400.0 million as of 2026Q3. [2]
- The credit agreement requires net leverage not to exceed 4.5x; actual net leverage was 1.7x as of 2026Q3. [3]
- Operating lease obligations due within the next two years total $71.0 million. [4]
- Purchase and other commitments due within the next two years total $42.6 million. [5]
- Floating-rate debt represented 30% of total debt after hedging. [6]
- Management evaluated events through the filing date and identified no material subsequent events. [7]
- The condensed consolidated financial statements were prepared in accordance with U.S. GAAP. [8]

**Computed ratios**
- Total debt / LTM EBITDA = 1.67x [9][10][11][12][13]
- EBITDA / interest expense (quarter) = 7.33x [13][14]
- Current assets / current liabilities = 1.50 [15][16]
- (Cash + 2 x max(FCF,0)) / debt maturing 2026-2028 = 2.67x (projection: flat FCF) [17][18][19][20][21][22]
- (Cash + revolver + 2 x max(FCF,0)) / (maturities 2026-2028 + leases + commitments + litigation + 2 x rate shock) = 2.45x (projection: flat FCF) [17][18][19][20][21][22][23][24][25][26][27][5][3][6][4]
- (Covenant limit - actual) / covenant limit = 0.62 [28][29][3]

*Judgment: the cited filings show a stable credit profile with no multi-period deterioration.* (model judgment, not a filing fact)

**Needs analyst review:** sector Industrials flagged industry-wide weakness via DRFT

[1] 10-Q filed 2026-10-28, Debt & maturity footnote (`alph-10-q-2026-10-28-debt_footnote`)
[2] 10-Q filed 2026-10-28, Item 7 / MD&A (Liquidity) (`alph-10-q-2026-10-28-item7`)
[3] 10-Q filed 2026-10-28, Note: Credit facilities & covenants (`alph-10-q-2026-10-28-note_credit`)
[4] 10-Q filed 2026-10-28, Note: Leases & off-balance-sheet arrangements (`alph-10-q-2026-10-28-note_leases`)
[5] 10-Q filed 2026-10-28, Note: Commitments & contingencies (`alph-10-q-2026-10-28-note_commitments`)
[6] 10-Q filed 2026-10-28, Note: Derivatives & interest-rate exposure (`alph-10-q-2026-10-28-note_derivatives`)
[7] 10-Q filed 2026-10-28, Note: Subsequent events (`alph-10-q-2026-10-28-note_subsequent`)
[8] 10-Q filed 2026-10-28, Note: Basis of presentation (going concern / restatement) (`alph-10-q-2026-10-28-note_basis`)
[9] XBRL fact `xbrl:0001000001:TotalDebt:2026Q3`
[10] XBRL fact `xbrl:0001000001:EBITDA:2025Q4`
[11] XBRL fact `xbrl:0001000001:EBITDA:2026Q1`
[12] XBRL fact `xbrl:0001000001:EBITDA:2026Q2`
[13] XBRL fact `xbrl:0001000001:EBITDA:2026Q3`
[14] XBRL fact `xbrl:0001000001:InterestExpense:2026Q3`
[15] XBRL fact `xbrl:0001000001:CurrentAssets:2026Q3`
[16] XBRL fact `xbrl:0001000001:CurrentLiabilities:2026Q3`
[17] XBRL fact `xbrl:0001000001:Cash:2026Q3`
[18] XBRL fact `xbrl:0001000001:OperatingCashFlow:FY2025`
[19] XBRL fact `xbrl:0001000001:Capex:FY2025`
[20] XBRL fact `xbrl:0001000001:DebtMaturity:2026`
[21] XBRL fact `xbrl:0001000001:DebtMaturity:2027`
[22] XBRL fact `xbrl:0001000001:DebtMaturity:2028`
[23] `note:0001000001:RevolverAvailability:2026Q3`
[24] `note:0001000001:LeaseObligations2Y:2026Q3`
[25] `note:0001000001:Commitments2Y:2026Q3`
[26] `note:0001000001:LitigationPossibleLoss:2026Q3`
[27] `note:0001000001:RateShockInterest:2026Q3`
[28] `note:0001000001:CovenantMax:2026Q3`
[29] `note:0001000001:CovenantActual:2026Q3`

### EMBR - Ember Energy Partners (Energy)
Score 0.64 | confidence high | interpretation: setback | scope: company | maturity wall 2028

**Filing facts (verbatim)**
- Total debt outstanding was $1,200.0 million as of 2026Q3. [1]
- Interest coverage, measured as EBITDA divided by interest expense, was 2.4x for the quarter ended 2026Q3. [2]
- Scheduled maturities: 2026 $100.0 million, 2027 $150.0 million, 2028 $200.0 million, 2029 $350.0 million, 2030 $400.0 million. [1]
- We had cash and cash equivalents of $500.0 million as of 2026Q3. [2]
- The credit agreement requires net leverage not to exceed 4.5x; actual net leverage was 2.0x as of 2026Q3. [3]
- Operating lease obligations due within the next two years total $60.0 million. [4]
- Purchase and other commitments due within the next two years total $36.0 million. [5]
- Floating-rate debt represented 30% of total debt after hedging. [6]
- Subsequent to quarter end, the Company began commissioning replacement processing capacity. [7]
- The condensed consolidated financial statements were prepared in accordance with U.S. GAAP. [8]
- Latest-quarter EBITDA included a one-time impairment charge related to the retirement of a processing facility. [2]
- Subsequent to quarter end, the Company began commissioning replacement processing capacity. [7]
- Management expects to recover the earnings impact as replacement capacity comes online. [2]

**Computed ratios**
- Total debt / LTM EBITDA = 1.98x [9][10][11][12][13]
- EBITDA / interest expense (quarter) = 2.40x [13][14]
- Current assets / current liabilities = 1.50 [15][16]
- (Cash + 2 x max(FCF,0)) / debt maturing 2026-2028 = 2.36x (projection: flat FCF) [17][18][19][20][21][22]
- (Cash + revolver + 2 x max(FCF,0)) / (maturities 2026-2028 + leases + commitments + litigation + 2 x rate shock) = 2.37x (projection: flat FCF) [17][18][19][20][21][22][23][24][25][26][27][5][3][6][4]
- (Covenant limit - actual) / covenant limit = 0.56 [28][29][3]

*Judgment: the latest weakness stems from a cited one-time item with a stated recovery plan.* (model judgment, not a filing fact)

**Needs analyst review:** subsequent event in the notes: “Subsequent to quarter end, the Company began commissioning replacement processing capacity.” [embr-10-q-2026-10-28-note_subsequent]; one-time setback accepted on cited evidence: confirm the recovery plan

[1] 10-Q filed 2026-10-28, Debt & maturity footnote (`embr-10-q-2026-10-28-debt_footnote`)
[2] 10-Q filed 2026-10-28, Item 7 / MD&A (Liquidity) (`embr-10-q-2026-10-28-item7`)
[3] 10-Q filed 2026-10-28, Note: Credit facilities & covenants (`embr-10-q-2026-10-28-note_credit`)
[4] 10-Q filed 2026-10-28, Note: Leases & off-balance-sheet arrangements (`embr-10-q-2026-10-28-note_leases`)
[5] 10-Q filed 2026-10-28, Note: Commitments & contingencies (`embr-10-q-2026-10-28-note_commitments`)
[6] 10-Q filed 2026-10-28, Note: Derivatives & interest-rate exposure (`embr-10-q-2026-10-28-note_derivatives`)
[7] 10-Q filed 2026-10-28, Note: Subsequent events (`embr-10-q-2026-10-28-note_subsequent`)
[8] 10-Q filed 2026-10-28, Note: Basis of presentation (going concern / restatement) (`embr-10-q-2026-10-28-note_basis`)
[9] XBRL fact `xbrl:0001000005:TotalDebt:2026Q3`
[10] XBRL fact `xbrl:0001000005:EBITDA:2025Q4`
[11] XBRL fact `xbrl:0001000005:EBITDA:2026Q1`
[12] XBRL fact `xbrl:0001000005:EBITDA:2026Q2`
[13] XBRL fact `xbrl:0001000005:EBITDA:2026Q3`
[14] XBRL fact `xbrl:0001000005:InterestExpense:2026Q3`
[15] XBRL fact `xbrl:0001000005:CurrentAssets:2026Q3`
[16] XBRL fact `xbrl:0001000005:CurrentLiabilities:2026Q3`
[17] XBRL fact `xbrl:0001000005:Cash:2026Q3`
[18] XBRL fact `xbrl:0001000005:OperatingCashFlow:FY2025`
[19] XBRL fact `xbrl:0001000005:Capex:FY2025`
[20] XBRL fact `xbrl:0001000005:DebtMaturity:2026`
[21] XBRL fact `xbrl:0001000005:DebtMaturity:2027`
[22] XBRL fact `xbrl:0001000005:DebtMaturity:2028`
[23] `note:0001000005:RevolverAvailability:2026Q3`
[24] `note:0001000005:LeaseObligations2Y:2026Q3`
[25] `note:0001000005:Commitments2Y:2026Q3`
[26] `note:0001000005:LitigationPossibleLoss:2026Q3`
[27] `note:0001000005:RateShockInterest:2026Q3`
[28] `note:0001000005:CovenantMax:2026Q3`
[29] `note:0001000005:CovenantActual:2026Q3`

### BRCK - Brickyard Foods Corp. (Consumer Staples)
Score 0.62 | confidence high | interpretation: stable | maturity wall 2028

**Filing facts (verbatim)**
- Total debt outstanding was $960.0 million as of 2026Q3. [1]
- Interest coverage, measured as EBITDA divided by interest expense, was 6.5x for the quarter ended 2026Q3. [2]
- Scheduled maturities: 2026 $60.0 million, 2027 $120.0 million, 2028 $150.0 million, 2029 $250.0 million, 2030 $380.0 million. [1]
- We had cash and cash equivalents of $250.0 million as of 2026Q3. [2]
- The credit agreement requires net leverage not to exceed 4.5x; actual net leverage was 1.9x as of 2026Q3. [3]
- Operating lease obligations due within the next two years total $48.0 million. [4]
- Purchase and other commitments due within the next two years total $28.8 million. [5]
- Floating-rate debt represented 30% of total debt after hedging. [6]
- Management evaluated events through the filing date and identified no material subsequent events. [7]
- The condensed consolidated financial statements were prepared in accordance with U.S. GAAP. [8]

**Computed ratios**
- Total debt / LTM EBITDA = 1.90x [9][10][11][12][13]
- EBITDA / interest expense (quarter) = 6.50x [13][14]
- Current assets / current liabilities = 1.43 [15][16]
- (Cash + 2 x max(FCF,0)) / debt maturing 2026-2028 = 2.12x (projection: flat FCF) [17][18][19][20][21][22]
- (Cash + revolver + 2 x max(FCF,0)) / (maturities 2026-2028 + leases + commitments + litigation + 2 x rate shock) = 2.00x (projection: flat FCF) [17][18][19][20][21][22][23][24][25][26][27][5][3][6][4]
- (Covenant limit - actual) / covenant limit = 0.58 [28][29][3]

*Judgment: the cited filings show a stable credit profile with no multi-period deterioration.* (model judgment, not a filing fact)

[1] 10-Q filed 2026-10-28, Debt & maturity footnote (`brck-10-q-2026-10-28-debt_footnote`)
[2] 10-Q filed 2026-10-28, Item 7 / MD&A (Liquidity) (`brck-10-q-2026-10-28-item7`)
[3] 10-Q filed 2026-10-28, Note: Credit facilities & covenants (`brck-10-q-2026-10-28-note_credit`)
[4] 10-Q filed 2026-10-28, Note: Leases & off-balance-sheet arrangements (`brck-10-q-2026-10-28-note_leases`)
[5] 10-Q filed 2026-10-28, Note: Commitments & contingencies (`brck-10-q-2026-10-28-note_commitments`)
[6] 10-Q filed 2026-10-28, Note: Derivatives & interest-rate exposure (`brck-10-q-2026-10-28-note_derivatives`)
[7] 10-Q filed 2026-10-28, Note: Subsequent events (`brck-10-q-2026-10-28-note_subsequent`)
[8] 10-Q filed 2026-10-28, Note: Basis of presentation (going concern / restatement) (`brck-10-q-2026-10-28-note_basis`)
[9] XBRL fact `xbrl:0001000002:TotalDebt:2026Q3`
[10] XBRL fact `xbrl:0001000002:EBITDA:2025Q4`
[11] XBRL fact `xbrl:0001000002:EBITDA:2026Q1`
[12] XBRL fact `xbrl:0001000002:EBITDA:2026Q2`
[13] XBRL fact `xbrl:0001000002:EBITDA:2026Q3`
[14] XBRL fact `xbrl:0001000002:InterestExpense:2026Q3`
[15] XBRL fact `xbrl:0001000002:CurrentAssets:2026Q3`
[16] XBRL fact `xbrl:0001000002:CurrentLiabilities:2026Q3`
[17] XBRL fact `xbrl:0001000002:Cash:2026Q3`
[18] XBRL fact `xbrl:0001000002:OperatingCashFlow:FY2025`
[19] XBRL fact `xbrl:0001000002:Capex:FY2025`
[20] XBRL fact `xbrl:0001000002:DebtMaturity:2026`
[21] XBRL fact `xbrl:0001000002:DebtMaturity:2027`
[22] XBRL fact `xbrl:0001000002:DebtMaturity:2028`
[23] `note:0001000002:RevolverAvailability:2026Q3`
[24] `note:0001000002:LeaseObligations2Y:2026Q3`
[25] `note:0001000002:Commitments2Y:2026Q3`
[26] `note:0001000002:LitigationPossibleLoss:2026Q3`
[27] `note:0001000002:RateShockInterest:2026Q3`
[28] `note:0001000002:CovenantMax:2026Q3`
[29] `note:0001000002:CovenantActual:2026Q3`

### LUMN - Lumina Utilities Co. (Utilities)
Score 0.45 | confidence high | interpretation: stable

**Filing facts (verbatim)**
- Total debt outstanding was $1,960.0 million as of 2026Q3. [1]
- Interest coverage, measured as EBITDA divided by interest expense, was 5.4x for the quarter ended 2026Q3. [2]
- Scheduled maturities: 2026 $100.0 million, 2027 $150.0 million, 2028 $250.0 million, 2029 $450.0 million, 2030 $1,000.0 million. [1]
- We had cash and cash equivalents of $300.0 million as of 2026Q3. [2]
- The credit agreement requires net leverage not to exceed 4.6x; actual net leverage was 2.6x as of 2026Q3. [3]
- Operating lease obligations due within the next two years total $180.0 million. [4]
- Purchase and other commitments due within the next two years total $58.8 million. [5]
- Floating-rate debt represented 55% of total debt after hedging. [6]
- Management evaluated events through the filing date and identified no material subsequent events. [7]
- The condensed consolidated financial statements were prepared in accordance with U.S. GAAP. [8]

**Computed ratios**
- Total debt / LTM EBITDA = 2.63x [9][10][11][12][13]
- EBITDA / interest expense (quarter) = 5.43x [13][14]
- Current assets / current liabilities = 1.23 [15][16]
- (Cash + 2 x max(FCF,0)) / debt maturing 2026-2028 = 1.36x (projection: flat FCF) [17][18][19][20][21][22]
- (Cash + revolver + 2 x max(FCF,0)) / (maturities 2026-2028 + leases + commitments + litigation + 2 x rate shock) = 1.09x (projection: flat FCF) [17][18][19][20][21][22][23][24][25][26][27][5][3][6][4]
- (Covenant limit - actual) / covenant limit = 0.43 [28][29][3]

*Judgment: the cited filings show a stable credit profile with no multi-period deterioration.* (model judgment, not a filing fact)

[1] 10-Q filed 2026-10-28, Debt & maturity footnote (`lumn-10-q-2026-10-28-debt_footnote`)
[2] 10-Q filed 2026-10-28, Item 7 / MD&A (Liquidity) (`lumn-10-q-2026-10-28-item7`)
[3] 10-Q filed 2026-10-28, Note: Credit facilities & covenants (`lumn-10-q-2026-10-28-note_credit`)
[4] 10-Q filed 2026-10-28, Note: Leases & off-balance-sheet arrangements (`lumn-10-q-2026-10-28-note_leases`)
[5] 10-Q filed 2026-10-28, Note: Commitments & contingencies (`lumn-10-q-2026-10-28-note_commitments`)
[6] 10-Q filed 2026-10-28, Note: Derivatives & interest-rate exposure (`lumn-10-q-2026-10-28-note_derivatives`)
[7] 10-Q filed 2026-10-28, Note: Subsequent events (`lumn-10-q-2026-10-28-note_subsequent`)
[8] 10-Q filed 2026-10-28, Note: Basis of presentation (going concern / restatement) (`lumn-10-q-2026-10-28-note_basis`)
[9] XBRL fact `xbrl:0001000012:TotalDebt:2026Q3`
[10] XBRL fact `xbrl:0001000012:EBITDA:2025Q4`
[11] XBRL fact `xbrl:0001000012:EBITDA:2026Q1`
[12] XBRL fact `xbrl:0001000012:EBITDA:2026Q2`
[13] XBRL fact `xbrl:0001000012:EBITDA:2026Q3`
[14] XBRL fact `xbrl:0001000012:InterestExpense:2026Q3`
[15] XBRL fact `xbrl:0001000012:CurrentAssets:2026Q3`
[16] XBRL fact `xbrl:0001000012:CurrentLiabilities:2026Q3`
[17] XBRL fact `xbrl:0001000012:Cash:2026Q3`
[18] XBRL fact `xbrl:0001000012:OperatingCashFlow:FY2025`
[19] XBRL fact `xbrl:0001000012:Capex:FY2025`
[20] XBRL fact `xbrl:0001000012:DebtMaturity:2026`
[21] XBRL fact `xbrl:0001000012:DebtMaturity:2027`
[22] XBRL fact `xbrl:0001000012:DebtMaturity:2028`
[23] `note:0001000012:RevolverAvailability:2026Q3`
[24] `note:0001000012:LeaseObligations2Y:2026Q3`
[25] `note:0001000012:Commitments2Y:2026Q3`
[26] `note:0001000012:LitigationPossibleLoss:2026Q3`
[27] `note:0001000012:RateShockInterest:2026Q3`
[28] `note:0001000012:CovenantMax:2026Q3`
[29] `note:0001000012:CovenantActual:2026Q3`

## Not selected

- **DRFT** [include]: Composite 0.18; interpretation: deterioration
- **FLUX** [prescreen_fail]: leverage 9.0x > 7.0x
- **GLEN** [exclude]: Hard veto: going-concern doubt cited (glen-10-k-2026-02-20-item1a)
- **HRBR** [uncertain]: post-filing event: covenant_waiver (8-K 2026-10-30)
- **IRON** [exclude]: Hard veto: liquidity cover 0.25 < 1.00: cash, revolver and projected free cash flow do not cover maturities, leases, commitments, litigation and rate shock over 2 years
- **JADE** [include]: Composite 0.58; interpretation: stable
- **KITE** [insufficient_data]: latest 10-Q filed 2026-03-15 is older than 150 days
- **MRDN** [include]: Composite 0.58; interpretation: stable

## Human review queue

- **ALPH** (include): sector Industrials flagged industry-wide weakness via DRFT
- **DRFT** (include): covenant headroom only 11% (credit-facility note); multi-period deterioration signal; weakness attributed to industry-wide conditions, not only this company
- **EMBR** (include): subsequent event in the notes: “Subsequent to quarter end, the Company began commissioning replacement processing capacity.” [embr-10-q-2026-10-28-note_subsequent]; one-time setback accepted on cited evidence: confirm the recovery plan
- **HRBR** (uncertain): post-filing event: covenant_waiver (8-K 2026-10-30)
- **JADE** (include): off-balance-sheet exposure in the notes: “Purchase and other commitments due within the next two years total $160.0 million, including $120.0 million of guarantees of an unconsolidated joint venture.” [jade-10-q-2026-10-28-note_commitments]
- **MRDN** (include): a figure was re-extracted from filing text after an implausible XBRL value; sector Industrials flagged industry-wide weakness via DRFT
- **NOVA** (include): subsequent event in the notes: “Subsequent to quarter end, the Company repaid $200.0 million of outstanding term loans.” [nova-10-q-2026-10-28-note_subsequent]
