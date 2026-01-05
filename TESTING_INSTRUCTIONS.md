# BP Market Hub - Testing Instructions

This document outlines the manual testing workflow for the Market Hub module.

## Prerequisites
- Ensure the module `bp_mwanzo_marketplace` is installed.
- Ensure you have access to the "Market Hub" menu (Group: Mwanzo Manager).

## Test Workflow

### 1. Configuration: Create Theme & Space
**Goal:** Set up the marketplace environment.
1. Navigate to **Market Hub > Configuration > Themes**.
2. Click **New**.
3. Enter a **Name** (e.g., "Holiday Market") and **Code** (e.g., "HOL2025").
4. Set **Start Date** and **End Date**.
5. Save.
6. Navigate to **Market Hub > Configuration > Market Spaces**.
7. Click **New**.
8. Enter a **Name** (e.g., "Stall A1") and **Code** (e.g., "A1").
9. Select a **Space Type** (e.g., Stall) and link it to the **Theme** created above.
10. Save.

### 2. Vendor Management: Create Vendor & License
**Goal:** Onboard a vendor and assign them a space.
1. Navigate to **Contacts** (or access via Marketplace).
2. Create or select a partner.
3. In the partner form, check the **Mwanzo Vendor** checkbox (this might be in a specific tab or the main view depending on customization).
4. Navigate to **Market Hub > Operations > Vendor Licenses**.
5. Click **New**.
6. Select the **Vendor** (must be a Mwanzo Vendor).
7. Select the **Theme** and **Space** created in Step 1.
8. Set **License Type** (e.g., Weekly) and dates.
9. Enter a **License Fee**.
10. Save and click **Create Invoice** (if available/required).

### 3. Inventory: Stock Intake
**Goal:** Receive goods from the vendor into consignment.
1. Navigate to **Market Hub > Operations > Stock Intake Sessions**.
2. Click **New**.
3. Select the **Vendor** and **Theme**.
4. Add lines for products the vendor is bringing.
   - *Note: Ensure products are configured if necessary.*
5. Click **Vendor Confirm** (simulating vendor agreement).
6. Click **Validate Intake**.
   - *Verification:* Check that stock moves have been generated and products are now in stock (likely with the vendor as the owner).

### 4. Sales: POS Transaction
**Goal:** Sell a vendor's product through the Point of Sale.
1. Open the **Point of Sale** dashboard.
2. Open a session.
3. Select a product associated with the Mwanzo Vendor/Theme.
   - *Note: You may need to ensure the product form has the "Mwanzo Vendor" and "Theme" fields set if the logic relies on product configuration, or if it relies on the stock owner.*
4. Complete the payment and validate the order.
5. Close the POS session.
   - *Verification:* Check the POS Order Lines in the backend. They should record the Vendor, Theme, and calculated Commission.

### 5. Accounting: Vendor Settlement
**Goal:** Calculate what is owed to the vendor after commissions.
1. Navigate to **Market Hub > Accounting > Vendor Settlement Wizard**.
2. Select the **Date Range** covering your POS sale.
3. Select the **Vendor** and **Theme**.
4. Click **Generate**.
5. Navigate to **Market Hub > Accounting > Vendor Statements**.
6. Open the newly created statement.
7. Verify:
   - **Total Sales**: Matches the POS sale amount.
   - **Total Commission**: Matches the expected commission percentage.
   - **Net Payable**: Sales minus Commission.
8. Click **Confirm**.
9. Click **Create Commission Invoice** (if applicable).

### 6. HR & Performance: Staff Commissions
**Goal:** Attribute sales to staff members.
1. Ensure POS orders created in Step 4 had a "Cashier" or "Salesperson" assigned.
2. The system is designed to run a scheduled action (Cron) to aggregate these.
3. You can manually trigger the cron or wait for the scheduled time.
   - Go to **Settings > Technical > Automation > Scheduled Actions**.
   - Find "Mwanzo: Generate Staff Commissions".
   - Click **Run Manually**.
4. Navigate to **Market Hub > HR & Performance** (or relevant menu) to view the generated commission entries.
