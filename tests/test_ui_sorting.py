import pytest
from playwright.async_api import async_playwright

@pytest.mark.asyncio
async def test_portfolio_table_sorting_and_filtering():
    async with async_playwright() as p:
        # Use system installed Edge or Chrome
        browser = None
        for channel in ["msedge", "chrome", "chromium"]:
            try:
                browser = await p.chromium.launch(channel=channel, headless=True)
                print(f"Launched browser with channel: {channel}")
                break
            except Exception as e:
                print(f"Channel {channel} failed: {e}")
        
        if not browser:
            print("Could not launch browser")
            sys.exit(1)

        page = await browser.new_page()
        print("Navigating to http://127.0.0.1:8000 ...")
        await page.goto("http://127.0.0.1:8000", wait_until="networkidle")
        
        # Wait for table rows to load
        await page.wait_for_selector("#portfolio-groups-tbody tr td:nth-child(4)")
        await page.wait_for_timeout(500)

        # 1. VERIFY DEFAULT SORTING (value_usd DESC)
        rows = await page.query_selector_all("#portfolio-groups-tbody tr")
        print(f"\n1. Default loaded rows count: {len(rows)}")
        
        first_row_asset = await rows[0].eval_on_selector("td:nth-child(1)", "el => el.innerText.trim()")
        first_row_usd = await rows[0].eval_on_selector("td:nth-child(4)", "el => el.innerText.trim()")
        print(f"  First row (Default sort): Asset='{first_row_asset}', USD='{first_row_usd}'")
        
        usd_values = []
        for r in rows[:10]:
            asset = await r.eval_on_selector("td:nth-child(1)", "el => el.innerText.trim()")
            usd_str = await r.eval_on_selector("td:nth-child(4)", "el => el.innerText.trim()")
            val = float(usd_str.replace('$', '').replace(',', '').strip() or 0)
            usd_values.append((asset, val))
            print(f"    - {asset:<12}: ${val:,.2f}")

        # Check that values are in descending order
        is_desc = all(usd_values[i][1] >= usd_values[i+1][1] for i in range(len(usd_values)-1))
        print(f"  --> Default sort is descending by USD: {is_desc} (PASS)")
        assert is_desc, "Default sort should be descending by USD"

        # 2. CLICK 'Omaisuuserä' (Group Name) -> SHOULD SORT A-Z
        print("\n2. Testing sort by Omaisuuserä (A-Z)...")
        th_name = await page.query_selector("th[data-sort='group_name']")
        await th_name.click()
        await page.wait_for_timeout(500)

        rows = await page.query_selector_all("#portfolio-groups-tbody tr")
        names_asc = []
        for r in rows[:6]:
            name = await r.eval_on_selector("td:nth-child(1)", "el => el.innerText.trim()")
            names_asc.append(name.split('\n')[-1].strip())
        print("  First 6 names after 1st click (asc):", names_asc)
        is_name_asc = all(names_asc[i].lower() <= names_asc[i+1].lower() for i in range(len(names_asc)-1))
        print(f"  --> Name asc sort: {is_name_asc} (PASS)")
        assert is_name_asc, "Group name should be ascending"

        # 3. CLICK 'Omaisuuserä' AGAIN -> SHOULD SORT Z-A
        print("\n3. Testing sort by Omaisuuserä (Z-A)...")
        await th_name.click()
        await page.wait_for_timeout(500)

        rows = await page.query_selector_all("#portfolio-groups-tbody tr")
        names_desc = []
        for r in rows[:6]:
            name = await r.eval_on_selector("td:nth-child(1)", "el => el.innerText.trim()")
            names_desc.append(name.split('\n')[-1].strip())
        print("  First 6 names after 2nd click (desc):", names_desc)
        is_name_desc = all(names_desc[i].lower() >= names_desc[i+1].lower() for i in range(len(names_desc)-1))
        print(f"  --> Name desc sort: {is_name_desc} (PASS)")
        assert is_name_desc, "Group name should be descending"

        # 4. CLICK 'Arvo (EUR)' -> SHOULD SORT DESCENDING EUR
        print("\n4. Testing sort by Arvo (EUR)...")
        th_eur = await page.query_selector("th[data-sort='value_eur']")
        await th_eur.click()
        await page.wait_for_timeout(500)

        rows = await page.query_selector_all("#portfolio-groups-tbody tr")
        eur_vals = []
        for r in rows[:6]:
            asset = await r.eval_on_selector("td:nth-child(1)", "el => el.innerText.trim()")
            eur_str = await r.eval_on_selector("td:nth-child(3)", "el => el.innerText.trim()")
            clean_eur = eur_str.replace('€', '').replace('\xa0', '').replace(' ', '').replace(',', '.').strip()
            eur_vals.append(float(clean_eur or 0))
        print("  First 6 EUR values after clicking EUR:", eur_vals)
        is_eur_desc = all(eur_vals[i] >= eur_vals[i+1] for i in range(len(eur_vals)-1))
        print(f"  --> EUR desc sort: {is_eur_desc} (PASS)")
        assert is_eur_desc, "EUR should be descending"

        # 5. TEST SEARCH FILTER
        print("\n5. Testing search filter ('BTC')...")
        search_input = await page.query_selector("#portfolio-group-search")
        await search_input.fill("BTC")
        await page.wait_for_timeout(500)

        rows = await page.query_selector_all("#portfolio-groups-tbody tr")
        print(f"  Rows count matching 'BTC': {len(rows)}")
        matched_assets = []
        for r in rows:
            asset = await r.eval_on_selector("td:nth-child(1)", "el => el.innerText.trim()")
            matched_assets.append(asset.split('\n')[-1].strip())
        print(f"  Matched assets: {matched_assets}")
        assert all("BTC" in m for m in matched_assets), "All matched rows should contain BTC"
        print("  --> Search filter: PASS")

        # Clear search
        await search_input.fill("")
        await page.wait_for_timeout(300)

        # 6. CLICK 'Arvo (USD)' AGAIN TO RETURN TO DEFAULT
        print("\n6. Clicking Arvo (USD)...")
        th_usd = await page.query_selector("th[data-sort='value_usd']")
        await th_usd.click()
        await page.wait_for_timeout(500)

        # Take screenshot of UI
        screenshot_path = "portfolio_ui_sorted.png"
        await page.screenshot(path=screenshot_path, full_page=True)
        print(f"\nScreenshot saved to {screenshot_path}")

        await browser.close()
        print("\n=== ALL FRONTEND INTERACTION TESTS PASSED! ===")

if __name__ == "__main__":
    asyncio.run(main())
