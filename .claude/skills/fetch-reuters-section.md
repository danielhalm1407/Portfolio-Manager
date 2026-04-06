## Fetch Reuters Section Headline

Navigate to a Reuters section page using Playwright MCP and open the top headline article.

**Usage:** `/fetch-reuters-section [section]`
- `section` defaults to `markets` if not specified
- Valid sections: `markets`, `business`, `world`, `sustainability`

### Steps

1. **Navigate** to the section page:
   ```
   browser_navigate → https://www.reuters.com/<section>/
   ```

2. **Scroll** to the article area (~1800px works for Markets; adjust if needed):
   ```
   browser_evaluate → () => { window.scrollTo(0, 1800); }
   ```

3. **Screenshot** to visually confirm the headline is visible:
   ```
   browser_take_screenshot
   ```
   Report the article title visible in the screenshot to the user before proceeding.

4. **Snapshot** to get element refs (will be saved to file — do NOT expect inline output):
   ```
   browser_snapshot
   ```
   The result is always saved to a file path shown in the error/output.

5. **Parse** the saved snapshot file for the headline link ref:
   ```bash
   python -c "
   import json
   with open(r'<saved_snapshot_path>') as f:
       data = json.load(f)
   text = data[0]['text']
   idx = text.find('<article_title_fragment>')
   print(repr(text[max(0,idx-300):idx+600]))
   "
   ```
   Extract the `ref=eXXX` value from the surrounding `link` element.

6. **Click** the headline link using the extracted ref:
   ```
   browser_click → ref=eXXX
   ```

7. **Report** the final article title and URL to the user.

### Output
Return to the user:
- Article title
- URL
- Author and publish date (visible after navigating to the article)
- Offer to scroll and read the article body if requested
