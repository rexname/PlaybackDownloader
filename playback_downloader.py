import asyncio
import json
import os
import re
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

from playwright.async_api import Browser, Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeout


class DeviceScraper:
    """Web scraper untuk download playback dari DVR/NVR"""

    def __init__(self, host: str = "192.168.88.19"):
        self.base_url = f"http://{host}"
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.playwright = None

        # Path configuration
        self.script_dir = Path(__file__).parent
        self.cookie_path = self.script_dir / "cookies.json"
        self.storage_path = self.script_dir / "storage.json"
        self.download_dir = self.script_dir / "downloads"
        self.organized_dir = self.download_dir / "cctv"
        self.log_file = self.download_dir / "log.txt"

        # Database file - akan di-set sesuai tanggal target download
        self.downloaded_files_db = None
        self.current_download_date = None

        # Download tracking
        self.pending_downloads = []
        self.completed_downloads = []
        # Structure: {"channels": {"1": {"pages": {"1": ["file1.mp4", "file2.mp4"]}}}}
        self.downloaded_files_db_data: Dict = {"channels": {}}
        self.current_download_batch = []  # Track download batch saat ini

        # Context tracking - untuk tahu file download dari channel/page mana
        self.current_channel = None
        self.current_page = None

        # Create directories
        self.download_dir.mkdir(exist_ok=True)
        self.organized_dir.mkdir(exist_ok=True)

        # Setup logging FIRST (sebelum load database yang pakai log)
        self.log_stream = open(self.log_file, "a", encoding="utf-8")

    def log(self, msg: str):
        """Log message to file"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_msg = f"[{timestamp}] {msg}"
        self.log_stream.write(log_msg + "\n")
        self.log_stream.flush()
        print(log_msg)

    def set_download_date(self, date_str: str):
        """Set tanggal download dan initialize database file
        Args:
            date_str: Format YYYY-MM-DD (misal: 2026-01-30)
        """
        try:
            # Parse tanggal
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            # Format ke DD-MM-YYYY untuk nama file
            formatted_date = date_obj.strftime("%d-%m-%Y")

            self.current_download_date = date_str
            self.downloaded_files_db = self.script_dir / f"files_{formatted_date}.json"

            self.log(f"[+] Database file set to: {self.downloaded_files_db.name}")

            # Load database untuk tanggal ini
            self.load_downloaded_files_db()

        except Exception as e:
            self.log(f"[-] Error setting download date: {str(e)}")

    def load_downloaded_files_db(self):
        """Load database of downloaded files"""
        if not self.downloaded_files_db:
            self.log("[*] Database file not set yet")
            return

        if self.downloaded_files_db.exists():
            try:
                with open(self.downloaded_files_db, "r") as f:
                    self.downloaded_files_db_data = json.load(f)

                # Ensure structure exists
                if "channels" not in self.downloaded_files_db_data:
                    self.downloaded_files_db_data = {"channels": {}}

                # Count total files
                total_files = 0
                for channel_data in self.downloaded_files_db_data["channels"].values():
                    for page_files in channel_data.get("pages", {}).values():
                        total_files += len(page_files)

                self.log(f"[+] Loaded database: {total_files} files across all channels/pages")
            except Exception as e:
                self.log(f"[-] Error loading database: {str(e)}")
                self.downloaded_files_db_data = {"channels": {}}
        else:
            self.log(f"[*] No previous database found for this date")
            self.downloaded_files_db_data = {"channels": {}}

    def save_downloaded_files_db(self, force_log: bool = False):
        """Save database of downloaded files
        Args:
            force_log: Force logging (default False untuk auto-save realtime)
        """
        if not self.downloaded_files_db:
            if force_log:
                self.log("[!] Cannot save - database file not set")
            return

        try:
            with open(self.downloaded_files_db, "w") as f:
                json.dump(self.downloaded_files_db_data, f, indent=2)

            # Log hanya jika force atau setiap 10 files
            if force_log:
                total_files = 0
                for channel_data in self.downloaded_files_db_data["channels"].values():
                    for page_files in channel_data.get("pages", {}).values():
                        total_files += len(page_files)

                self.log(f"[+] Saved database: {total_files} files")
        except Exception as e:
            self.log(f"[-] Error saving database: {str(e)}")

    def is_file_downloaded(self, channel: int, page: int, filename: str) -> bool:
        """Check if file sudah pernah didownload
        Args:
            channel: Channel number (0-based index atau channel value)
            page: Page number
            filename: Nama file
        """
        channel_key = str(channel)
        page_key = str(page)

        if channel_key not in self.downloaded_files_db_data["channels"]:
            return False

        channel_data = self.downloaded_files_db_data["channels"][channel_key]

        if "pages" not in channel_data:
            return False

        if page_key not in channel_data["pages"]:
            return False

        return filename in channel_data["pages"][page_key]

    def mark_file_downloaded(self, channel: int, page: int, filename: str):
        """Mark file sebagai downloaded
        Args:
            channel: Channel number
            page: Page number
            filename: Nama file
        """
        channel_key = str(channel)
        page_key = str(page)

        # Ensure structure exists
        if channel_key not in self.downloaded_files_db_data["channels"]:
            self.downloaded_files_db_data["channels"][channel_key] = {"pages": {}}

        channel_data = self.downloaded_files_db_data["channels"][channel_key]

        if "pages" not in channel_data:
            channel_data["pages"] = {}

        if page_key not in channel_data["pages"]:
            channel_data["pages"][page_key] = []

        # Add filename jika belum ada
        if filename not in channel_data["pages"][page_key]:
            channel_data["pages"][page_key].append(filename)

            # Auto-save realtime setiap file (tanpa log spam)
            self.save_downloaded_files_db(force_log=False)

    def get_page_stats(self, channel: int, page: int) -> dict:
        """Get statistik download untuk page tertentu
        Returns:
            dict dengan keys: downloaded_count, files (list of filenames)
        """
        channel_key = str(channel)
        page_key = str(page)

        if channel_key not in self.downloaded_files_db_data["channels"]:
            return {"downloaded_count": 0, "files": []}

        channel_data = self.downloaded_files_db_data["channels"][channel_key]

        if "pages" not in channel_data or page_key not in channel_data["pages"]:
            return {"downloaded_count": 0, "files": []}

        files = channel_data["pages"][page_key]
        return {"downloaded_count": len(files), "files": files}
        if len(self.downloaded_files_set) % 10 == 0:
            self.save_downloaded_files_db(force_log=True)

    async def save_cookies(self):
        """Save browser cookies to file"""
        cookies = await self.page.context.cookies()
        with open(self.cookie_path, "w") as f:
            json.dump(cookies, f, indent=2)
        self.log("[+] Cookies saved")

    async def load_cookies(self) -> bool:
        """Load cookies from file"""
        if self.cookie_path.exists():
            with open(self.cookie_path, "r") as f:
                cookies = json.load(f)
            await self.page.context.add_cookies(cookies)
            self.log("[+] Cookies loaded")
            return True
        return False

    async def save_local_storage(self):
        """Save localStorage to file"""
        storage = await self.page.evaluate("() => JSON.stringify(localStorage)")
        with open(self.storage_path, "w") as f:
            f.write(storage)
        self.log("[+] LocalStorage saved")

    async def load_local_storage(self) -> bool:
        """Load localStorage from file"""
        if self.storage_path.exists():
            with open(self.storage_path, "r") as f:
                storage = f.read()

            await self.page.evaluate(
                f"""(storage) => {{
                Object.entries(JSON.parse(storage)).forEach(([key, value]) => {{
                    localStorage.setItem(key, value);
                }});
            }}""",
                storage,
            )
            self.log("[+] LocalStorage loaded")
            return True
        return False

    async def check_session(self) -> bool:
        """Check if session is still valid"""
        try:
            # Check jika masih di halaman login
            current_url = self.page.url
            if "login" in current_url:
                self.log("[-] Session expired - redirected to login")
                return False

            # Check jika user logo masih ada
            try:
                await self.page.wait_for_selector("#main_user_logo", timeout=3000)
                self.log("[+] Session still valid")
                return True
            except PlaywrightTimeout:
                self.log("[-] Session invalid - user logo not found")
                return False

        except Exception as e:
            self.log(f"[-] Error checking session: {str(e)}")
            return False

    async def re_login_and_resume(self, channel_value: int, page_num: int, start_date: str, end_date: str) -> bool:
        """Re-login and resume to specific channel and page"""
        try:
            self.log(f"[*] Re-login and resume to channel {channel_value}, page {page_num}")

            # Add delay before re-login attempt to avoid overwhelming server
            await asyncio.sleep(3)

            # Login with retry mechanism
            if not await self.login("scrapper", "sc@10001"):
                self.log("[-] Re-login failed after all retries")
                return False

            self.log("[+] Re-login successful")
            await asyncio.sleep(2)  # Increased wait after login

            # Navigate to playback menu
            self.log("[*] Navigating to playback menu...")
            await self.click_xpath('//*[@id="main_playback"]')
            await asyncio.sleep(2)  # Increased delay
            await self.click_xpath('//*[@id="playback_new_download"]')
            self.log("[+] Entered playback menu")
            await asyncio.sleep(2)  # Increased delay

            # Select channel
            if not await self.select_channel(channel_value):
                self.log(f"[-] Failed to re-select channel {channel_value}")
                return False

            await asyncio.sleep(1)

            # Set date range
            if not await self.set_date_range(start_date, end_date):
                self.log("[-] Failed to re-set date range")
                return False

            await asyncio.sleep(1)

            # Query playback
            if not await self.query_playback():
                self.log("[-] Failed to re-query playback")
                return False

            await asyncio.sleep(1)

            # Navigate to page if not page 1
            if page_num > 1:
                self.log(f"[*] Navigating back to page {page_num}...")
                await self.page.evaluate(
                    f"""(targetPage) => {{
                    const input = document.getElementById('playback_jump_page');
                    const btn = document.getElementById('playback_jump');
                    if (input && btn) {{
                        input.value = targetPage;
                        btn.click();
                    }}
                }}""",
                    page_num,
                )
                await asyncio.sleep(3)  # Increased delay for page navigation

            self.log(f"[+] Successfully resumed to channel {channel_value}, page {page_num}")
            return True

        except Exception as e:
            self.log(f"[-] Error in re_login_and_resume: {str(e)}")
            return False

    async def initialize(self):
        """Initialize browser and page"""
        self.log("[*] Launching browser...")

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        # Create context with download path
        context = await self.browser.new_context(
            accept_downloads=True,
        )

        self.page = await context.new_page()

        # Setup download handler
        self.page.on("download", self._handle_download)

        self.log(f"[+] Browser ready, downloads to: {self.download_dir}")

    async def _handle_download(self, download):
        """Handle file download"""
        try:
            # Add to pending downloads
            self.pending_downloads.append(download)

            # Get suggested filename
            filename = download.suggested_filename
            save_path = self.download_dir / filename

            # Check if file already downloaded before (di DB) untuk channel/page ini
            if self.current_channel is not None and self.current_page is not None:
                if self.is_file_downloaded(self.current_channel, self.current_page, filename):
                    self.log(f"[SKIP] Already in DB - Ch{self.current_channel} P{self.current_page}: {filename}")
                    if download in self.pending_downloads:
                        self.pending_downloads.remove(download)
                    await download.cancel()
                    return

            # Check if file already exists in organized directory
            if self.check_file_exists(filename):
                self.log(f"[SKIP] File exists in organized dir: {filename}")
                # Mark as downloaded in DB
                if self.current_channel is not None and self.current_page is not None:
                    self.mark_file_downloaded(self.current_channel, self.current_page, filename)
                if download in self.pending_downloads:
                    self.pending_downloads.remove(download)
                self.completed_downloads.append(filename)
                await download.cancel()
                return

            self.log(f"[*] Downloading: {filename}")
            self.log(f"[*] Save path: {save_path}")

            # Save the download
            await download.save_as(str(save_path))

            # Verify file exists and has size
            if save_path.exists():
                file_size = save_path.stat().st_size
                if file_size > 0:
                    self.log(f"[+] File saved: {filename} ({file_size} bytes)")

                    # Mark as downloaded in DB with channel/page context
                    if self.current_channel is not None and self.current_page is not None:
                        self.mark_file_downloaded(self.current_channel, self.current_page, filename)
                        # Note: save sudah dipanggil di dalam mark_file_downloaded()

                    # Move to completed
                    if download in self.pending_downloads:
                        self.pending_downloads.remove(download)
                    self.completed_downloads.append(filename)
                    self.current_download_batch.append(filename)

                    self.log(
                        f"[+] Download completed: {filename} (Total session: {len(self.completed_downloads)})"
                    )

                    # Organize file immediately (real-time)
                    await self._organize_single_file(filename)
                else:
                    self.log(f"[-] WARNING: File has 0 bytes: {save_path}")
                    save_path.unlink()  # Delete empty file
                    if download in self.pending_downloads:
                        self.pending_downloads.remove(download)
            else:
                self.log(f"[-] WARNING: File not found after save: {save_path}")
                if download in self.pending_downloads:
                    self.pending_downloads.remove(download)

        except Exception as e:
            self.log(
                f"[-] Error saving download {filename if 'filename' in locals() else 'unknown'}: {str(e)}"
            )
            import traceback

            self.log(f"[-] Traceback: {traceback.format_exc()}")
            if download in self.pending_downloads:
                self.pending_downloads.remove(download)

    async def _organize_single_file(self, filename: str):
        """Organize a single downloaded file immediately"""
        try:
            if not filename.endswith(".mp4"):
                return

            # Parse filename: 192.168.88.19_1_20250129004231_20250129004252.mp4
            # Format: IP_CHANNEL_STARTTIME_ENDTIME.mp4
            # Time format: YYYYMMDDHHmmss
            full_match = re.search(
                r"\d+\.\d+\.\d+\.\d+_(\d{3})_(\d{14})([A-F0-9]{4})\.mp4$",
                filename,
            )

            if not full_match:
                self.log(f"[-] Could not parse filename: {filename}")
                return

            # Extract components
            channel_num = int(full_match.group(1))
            timestamp = full_match.group(2)
            # random_hex = full_match.group(3)  # not used

            # Parse timestamp
            start_year = timestamp[0:4]
            start_month = timestamp[4:6]
            start_day = timestamp[6:8]
            start_hour = timestamp[8:10]
            start_minute = timestamp[10:12]
            start_second = timestamp[12:14]

            # Format components
            date_str = f"{start_year}-{start_month}-{start_day}"
            start_formatted = f"{start_hour}-{start_minute}-{start_second}"

            # Create channel folder
            channel_folder = self.organized_dir / f"channel{channel_num}"
            channel_folder.mkdir(exist_ok=True)

            # Generate new filename
            new_filename = f"{date_str}.{start_formatted}.mp4"

            old_path = self.download_dir / filename
            new_path = channel_folder / new_filename

            # Move file
            if old_path.exists():
                os.rename(old_path, new_path)
                self.log(
                    f"[✓] Organized: {filename} → channel{channel_num}/{new_filename}"
                )
            else:
                self.log(f"[-] File not found for organization: {filename}")

        except Exception as e:
            self.log(f"[-] Error organizing {filename}: {str(e)}")
            import traceback

            self.log(f"[-] Traceback: {traceback.format_exc()}")

    async def login(
        self, username: str = "scrapper", password: str = "sc@10001"
    ) -> bool:
        """Login to device with retry mechanism"""
        max_retries = 3
        base_delay = 5  # Base delay in seconds

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    # Exponential backoff: 5s, 10s, 20s
                    delay = base_delay * (2 ** attempt)
                    self.log(f"[*] Retry attempt {attempt + 1}/{max_retries} after {delay}s delay...")
                    await asyncio.sleep(delay)

                login_url = f"{self.base_url}/"

                self.log(f"[*] Navigating to {login_url}")
                await self.page.goto(login_url, timeout=45000)
                await self.page.reload(wait_until="networkidle")

                # Wait for login hash in URL - increased timeout
                await self.page.wait_for_function(
                    "() => window.location.hash.includes('login')", timeout=30000
                )
                await asyncio.sleep(1)

                self.log("[*] Waiting for login form...")
                await self.page.wait_for_selector("#login_u", timeout=30000)
                self.log("[+] Login form found")

                self.log("[*] Pause 2 detik - tunggu form siap...")
                await asyncio.sleep(2)

                self.log(f"[*] Entering username: {username}")
                await self.page.type("#login_u", username, delay=100)
                await asyncio.sleep(0.2)

                self.log(f"[*] Entering password: {'*' * len(password)}")
                await self.page.click("#login_p")
                await asyncio.sleep(0.5)
                await self.page.type("#login_p", password, delay=100)
                await asyncio.sleep(1)

                self.log("[*] Clicking login button...")
                await self.page.click("#login_s")

                # Wait for redirect - increased timeout
                self.log("[*] Waiting for redirect...")
                await self.page.wait_for_function(
                    "() => !window.location.hash.includes('login')", timeout=30000
                )
                await asyncio.sleep(1)

                current_url = self.page.url
                self.log(f"[*] Current URL: {current_url}")

                # Verify login - increased timeout
                try:
                    await self.page.wait_for_selector("#main_user_logo", timeout=15000)
                    self.log("[+] Login berhasil!")
                    await asyncio.sleep(1.5)
                    return True
                except PlaywrightTimeout:
                    self.log("[-] Login gagal - element login tidak ditemukan")
                    if attempt < max_retries - 1:
                        continue
                    return False

            except PlaywrightTimeout as timeout_error:
                self.log(f"[-] Timeout error on attempt {attempt + 1}: {str(timeout_error)}")
                if attempt < max_retries - 1:
                    continue
                return False
            except Exception as error:
                self.log(f"[-] Error on attempt {attempt + 1}: {str(error)}")
                if attempt < max_retries - 1:
                    continue
                return False

        self.log(f"[-] Login failed after {max_retries} attempts")
        return False

    async def click_xpath(self, xpath: str) -> bool:
        """Click element by XPath"""
        try:
            await self.page.evaluate(
                f"""(xpath) => {{
                const element = document.evaluate(
                    xpath,
                    document,
                    null,
                    XPathResult.FIRST_ORDERED_NODE_TYPE,
                    null
                ).singleNodeValue;
                if (element) {{
                    element.click();
                }}
            }}""",
                xpath,
            )
            self.log(f"[+] Clicked element: {xpath}")
            return True
        except Exception as error:
            self.log(f"[-] Error clicking XPath: {str(error)}")
            return False

    async def get_channel_list(self) -> List[Dict]:
        """Get list of available channels"""
        try:
            self.log("[*] Extracting channel list...")
            channels = await self.page.evaluate("""() => {
                const select = document.getElementById('playback_down_channel');
                if (!select) return [];
                const options = Array.from(select.options);
                return options.map(opt => ({
                    value: parseInt(opt.value),
                    label: opt.textContent.trim()
                }));
            }""")
            self.log(f"[+] Found {len(channels)} channels")
            return channels
        except Exception as error:
            self.log(f"[-] Error extracting channels: {str(error)}")
            return []

    async def select_channel(self, channel_value: int) -> bool:
        """Select a channel"""
        try:
            await self.page.select_option("#playback_down_channel", str(channel_value))
            self.log(f"[+] Selected channel value: {channel_value}")
            await asyncio.sleep(0.5)
            return True
        except Exception as error:
            self.log(f"[-] Error selecting channel: {str(error)}")
            return False

    async def set_date_range(self, start_date: str, end_date: str) -> bool:
        """Set date range for playback query"""
        try:
            self.log(f"[*] Setting date range: {start_date} to {end_date}")

            # Set start date
            await self.page.evaluate("""() => {
                const elem = document.getElementById('playback_down_start');
                elem.value = '';
                elem.focus();
            }""")
            await self.page.type("#playback_down_start", start_date, delay=50)
            await asyncio.sleep(0.3)

            # Set end date
            await self.page.evaluate("""() => {
                const elem = document.getElementById('playback_down_end');
                elem.value = '';
                elem.focus();
            }""")
            await self.page.type("#playback_down_end", end_date, delay=50)
            await asyncio.sleep(0.3)

            self.log("[+] Date range set")
            return True
        except Exception as error:
            self.log(f"[-] Error setting date range: {str(error)}")
            return False

    async def query_playback(self) -> bool:
        """Query playback recordings"""
        try:
            self.log("[*] Querying playback...")
            await self.click_xpath('//*[@id="playback_down_query"]')

            # Wait for table to load
            await asyncio.sleep(2)

            # Check if table exists
            table_exists = await self.page.evaluate("""() => {
                const table = document.querySelector('div.td-table-body');
                return table ? true : false;
            }""")

            if not table_exists:
                self.log("[-] No results table found")
                return False

            self.log("[+] Query completed")
            return True
        except Exception as error:
            self.log(f"[-] Error querying playback: {str(error)}")
            return False

    async def extract_table_data(self) -> List[Dict]:
        """Extract data from results table"""
        try:
            self.log("[*] Extracting table data...")

            table_data = await self.page.evaluate("""() => {
                const rows = Array.from(
                    document.querySelectorAll('div.td-table-body div.td-table-row')
                );
                return rows.map(row => {
                    const cells = Array.from(row.querySelectorAll('span.td-table-cell'));
                    return {
                        channel: cells[1]?.textContent?.trim() || '',
                        startTime: cells[2]?.textContent?.trim() || '',
                        endTime: cells[3]?.textContent?.trim() || '',
                        type: cells[4]?.textContent?.trim() || '',
                        lock: cells[5]?.textContent?.trim() || ''
                    };
                });
            }""")

            self.log(f"[+] Extracted {len(table_data)} files from table")
            return table_data
        except Exception as error:
            self.log(f"[-] Error extracting table data: {str(error)}")
            return []

    async def get_pagination_info(self) -> Dict:
        """Get current pagination information"""
        try:
            page_info = await self.page.evaluate("""() => {
                const currentPageElem = document.getElementById('playback_pagecur');
                const totalPageElem = document.getElementById('playback_pagecount');

                return {
                    current: currentPageElem ? parseInt(currentPageElem.textContent) : 1,
                    total: totalPageElem ? parseInt(totalPageElem.textContent) : 1
                };
            }""")

            self.log(
                f"[*] Pagination: Page {page_info['current']} of {page_info['total']}"
            )
            return page_info
        except Exception as error:
            self.log(f"[-] Error getting pagination info: {str(error)}")
            return {"current": 1, "total": 1}

    async def get_failed_file_indices(self) -> List[int]:
        """Get indices of files that are still checked (failed files remain checked)"""
        try:
            indices = await self.page.evaluate("""() => {
                const rows = Array.from(
                    document.querySelectorAll('div.td-table-body div.td-table-row')
                );
                const failedIndices = [];
                rows.forEach((row, index) => {
                    const checkbox = row.querySelector('input.checkbox');
                    if (checkbox && checkbox.checked) {
                        failedIndices.push(index);
                    }
                });
                return failedIndices;
            }""")

            if len(indices) > 0:
                self.log(f"[*] Found {len(indices)} files still selected (likely failed): {indices}")

            return indices
        except Exception as error:
            self.log(f"[-] Error getting failed file indices: {str(error)}")
            return []

    async def select_all_files(self) -> bool:
        """Select all files on current page"""
        try:
            self.log("[*] Selecting all files on this page...")

            checkbox_clicked = await self.page.evaluate("""() => {
                const checkbox = document.querySelector('div.td-table-header input.checkbox');
                if (checkbox) {
                    checkbox.click();
                    return true;
                }
                return false;
            }""")

            if not checkbox_clicked:
                self.log("[-] Checkbox not found")
                return False

            await asyncio.sleep(0.5)
            self.log("[+] All files selected")
            return True
        except Exception as error:
            self.log(f"[-] Error selecting all files: {str(error)}")
            return False

    async def select_specific_files(self, indices: List[int]) -> bool:
        """Select only specific files by their indices"""
        try:
            if not indices:
                self.log("[-] No indices provided")
                return False

            self.log(f"[*] Selecting {len(indices)} specific files: {indices}")

            selected = await self.page.evaluate("""(indices) => {
                const rows = Array.from(
                    document.querySelectorAll('div.td-table-body div.td-table-row')
                );
                let count = 0;
                indices.forEach(index => {
                    if (index < rows.length) {
                        const checkbox = rows[index].querySelector('input.checkbox');
                        if (checkbox && !checkbox.checked) {
                            checkbox.click();
                            count++;
                        }
                    }
                });
                return count;
            }""", indices)

            await asyncio.sleep(0.5)
            self.log(f"[+] Selected {selected} files")
            return selected > 0
        except Exception as error:
            self.log(f"[-] Error selecting specific files: {str(error)}")
            return False

    async def start_download(self) -> bool:
        """Start download process"""
        try:
            self.log("[*] Starting download...")

            # Reset current batch tracking
            self.current_download_batch = []

            await self.click_xpath('//*[@id="playback_start_download"]')
            await asyncio.sleep(1)
            self.log("[+] Download button clicked")
            return True
        except Exception as error:
            self.log(f"[-] Error starting download: {str(error)}")
            return False

    async def wait_for_download_completion(self, timeout_ms: int = 600000) -> dict:
        """Wait for download to complete, returns dict with success/failure count"""
        try:
            self.log("[*] Waiting for download to complete...")
            start_time = asyncio.get_event_loop().time()
            timeout_sec = timeout_ms / 1000

            # Track initial state
            initial_download_count = len(self.completed_downloads)
            batch_start_count = len(self.current_download_batch)

            # Wait initial delay
            await asyncio.sleep(2)

            last_progress = ""
            no_change_count = 0
            last_progress_time = asyncio.get_event_loop().time()
            expected_files = 0
            result = {"success": 0, "failure": 0, "completed": False}

            while asyncio.get_event_loop().time() - start_time < timeout_sec:
                # Check session masih valid
                if not await self.check_session():
                    self.log("[!] Session lost during download - aborting")
                    result["completed"] = False
                    return result

                status = await self.page.evaluate("""() => {
                    const infoElem = document.getElementById('playback_down_info');
                    const stopBtn = document.getElementById('playback_down_stop');
                    const progressBar = document.getElementById('playback_down_progress');
                    const alertElem = document.getElementById('info_');
                    const showbox = document.getElementById('showbox');

                    return {
                        infoText: infoElem ? infoElem.textContent.trim() : null,
                        stopBtnExists: stopBtn ? true : false,
                        progressWidth: progressBar ? progressBar.style.width : null,
                        alertVisible: alertElem ? alertElem.style.display !== 'none' : false,
                        alertText: showbox ? showbox.textContent.trim() : null
                    };
                }""")

                # Check for success alert
                if status["alertVisible"] and status["alertText"]:
                    self.log(f"[+] Download alert: {status['alertText']}")

                    success_match = re.search(r"Success (\d+)", status["alertText"])
                    failure_match = re.search(r"Failure (\d+)", status["alertText"])

                    if success_match and failure_match:
                        success = int(success_match.group(1))
                        failure = int(failure_match.group(1))
                        expected_files = success
                        result["success"] = success
                        result["failure"] = failure
                        result["completed"] = True
                        self.log(
                            f"[+] Download initiated: {success} success, {failure} failure"
                        )

                        # Wait for actual file downloads to complete
                        self.log("[*] Waiting for files to finish downloading...")
                        for wait_count in range(60):  # Wait up to 60 seconds
                            current_batch_downloads = len(self.current_download_batch)

                            if (
                                current_batch_downloads >= expected_files
                                and len(self.pending_downloads) == 0
                            ):
                                self.log(
                                    f"[+] All {current_batch_downloads} files downloaded successfully"
                                )
                                # Save DB setelah batch selesai
                                self.save_downloaded_files_db(force_log=True)
                                return result

                            if wait_count % 5 == 0:  # Log every 5 seconds
                                self.log(
                                    f"[*] Downloaded {current_batch_downloads}/{expected_files} files, {len(self.pending_downloads)} pending"
                                )

                            await asyncio.sleep(1)

                        self.log(
                            f"[+] Download completed with {len(self.current_download_batch)} files"
                        )
                        # Save DB
                        self.save_downloaded_files_db(force_log=True)
                        return result

                # Check progress text
                if status["infoText"]:
                    if status["infoText"] != last_progress:
                        self.log(f"[*] {status['infoText']}")
                        last_progress = status["infoText"]
                        no_change_count = 0
                        last_progress_time = asyncio.get_event_loop().time()
                    else:
                        no_change_count += 1

                    # Parse progress
                    match = re.search(r"\((\d+)/(\d+)\)", status["infoText"])
                    if match:
                        current = int(match.group(1))
                        total = int(match.group(2))

                        if current == total and total > 0:
                            self.log(
                                f"[+] All files queued for download ({current}/{total})"
                            )
                            expected_files = total

                            # Wait for alert
                            for _ in range(60):
                                alert_showing = await self.page.evaluate("""() => {
                                    const alert = document.getElementById('info_');
                                    return alert ? alert.style.display !== 'none' : false;
                                }""")

                                if alert_showing:
                                    await asyncio.sleep(1)
                                    result_text = await self.page.evaluate("""() => {
                                        const showbox = document.getElementById('showbox');
                                        return showbox ? showbox.textContent.trim() : null;
                                    }""")
                                    self.log(f"[+] {result_text}")

                                    # Parse result
                                    success_match = re.search(
                                        r"Success (\d+)", result_text
                                    )
                                    failure_match = re.search(
                                        r"Failure (\d+)", result_text
                                    )
                                    if success_match and failure_match:
                                        result["success"] = int(success_match.group(1))
                                        result["failure"] = int(failure_match.group(1))
                                        result["completed"] = True

                                    break

                                await asyncio.sleep(1)

                            # Now wait for actual downloads
                            self.log("[*] Waiting for actual file downloads...")
                            for wait_count in range(120):  # Wait up to 2 minutes
                                current_batch_downloads = len(self.current_download_batch)

                                if (
                                    current_batch_downloads >= expected_files
                                    and len(self.pending_downloads) == 0
                                ):
                                    self.log(
                                        f"[+] All {current_batch_downloads} files downloaded successfully"
                                    )
                                    # Save DB dengan logging
                                    self.save_downloaded_files_db(force_log=True)
                                    return result

                                if wait_count % 10 == 0:  # Log every 10 seconds
                                    self.log(
                                        f"[*] Downloaded {current_batch_downloads}/{expected_files} files, {len(self.pending_downloads)} pending"
                                    )

                                await asyncio.sleep(1)

                            self.log(
                                f"[+] Download finished with {len(self.current_download_batch)} files"
                            )
                            # Save DB
                            self.save_downloaded_files_db(force_log=True)
                            return result

                    # Check for stalled progress
                    time_since_change = (
                        asyncio.get_event_loop().time() - last_progress_time
                    )
                    if (
                        no_change_count > 30
                        and time_since_change > 60
                        and not status["stopBtnExists"]
                    ):
                        self.log("[!] Download progress unchanged for 60s, completing")
                        result["completed"] = True
                        # Save DB
                        self.save_downloaded_files_db(force_log=True)
                        return result

                # Check if stop button disappeared
                if not status["stopBtnExists"] and status["infoText"] is None:
                    self.log("[+] Download completed (stop button disappeared)")
                    # Still wait a bit for downloads
                    await asyncio.sleep(5)
                    result["completed"] = True
                    # Save DB
                    self.save_downloaded_files_db(force_log=True)
                    return result

                await asyncio.sleep(2)

            self.log("[-] Download timeout")
            result["completed"] = False
            # Save DB even on timeout
            self.save_downloaded_files_db(force_log=True)
            return result
        except Exception as error:
            self.log(f"[-] Error waiting for download completion: {str(error)}")
            # Save DB on error
            self.save_downloaded_files_db(force_log=True)
            return {"success": 0, "failure": 0, "completed": False}

    async def close(self):
        """Close browser and cleanup"""
        # Final save of download DB
        self.save_downloaded_files_db(force_log=True)

        if self.browser:
            await self.browser.close()
            self.log("[+] Browser closed")
        if self.playwright:
            await self.playwright.stop()
        if self.log_stream:
            self.log_stream.close()

    def get_download_stats(self):
        """Get current download statistics"""
        # Count total files in database
        total_in_db = 0
        for channel_data in self.downloaded_files_db_data["channels"].values():
            for page_files in channel_data.get("pages", {}).values():
                total_in_db += len(page_files)

        return {
            "completed": len(self.completed_downloads),
            "pending": len(self.pending_downloads),
            "total_ever": total_in_db,
            "files": self.completed_downloads,
        }

    def check_file_exists(self, filename: str) -> bool:
        """Check if organized file already exists based on filename pattern"""
        try:
            if not filename.endswith(".mp4"):
                return False

            # Parse filename to get expected organized path
            full_match = re.search(
                r"\d+\.\d+\.\d+\.\d+_(\d+)_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\.mp4$",
                filename,
            )

            if not full_match:
                return False

            channel_num = int(full_match.group(1))

            # Start time
            start_year = full_match.group(2)
            start_month = full_match.group(3)
            start_day = full_match.group(4)
            start_hour = full_match.group(5)
            start_minute = full_match.group(6)
            start_second = full_match.group(7)

            # Expected organized filename
            date_str = f"{start_year}-{start_month}-{start_day}"
            start_formatted = f"{start_hour}-{start_minute}-{start_second}"
            expected_filename = f"{date_str}.{start_formatted}.mp4"

            # Check if file exists in organized directory
            channel_folder = self.organized_dir / f"channel{channel_num}"
            expected_path = channel_folder / expected_filename

            return expected_path.exists()

        except Exception as e:
            return False


async def download_playback(scraper: DeviceScraper):
    """Main download playback flow"""
    try:
        scraper.log("\n[*] === Starting Download Playback Flow ===\n")

        # Get all channels
        channels = await scraper.get_channel_list()
        if not channels:
            scraper.log("[-] No channels found")
            return

        # Filter active channels [1] through [21]
        active_channels = []
        for ch in channels:
            match = re.match(r"^\[(\d+)\]", ch["label"])
            if match:
                num = int(match.group(1))
                if 1 <= num <= 21:
                    active_channels.append(ch)

        scraper.log(
            f"[+] Active channels: {len(active_channels)} (filtered from {len(channels)})"
        )
        for ch in active_channels[:3]:
            scraper.log(f"    - Value: {ch['value']}, Label: {ch['label']}")

        # Set date range - yesterday 24 hours
        yesterday = datetime.now() - timedelta(days=1)
        date_str = yesterday.strftime("%Y-%m-%d")
        start_date = f"{date_str} 00:00:00"
        end_date = f"{date_str} 23:59:59"

        # Set download date untuk database file
        scraper.set_download_date(date_str)

        # Loop through all active channels
        for channel in active_channels:
            scraper.log(f"\n[*] Memproses channel: {channel['label']}")

            # Set current channel context
            scraper.current_channel = channel["value"]

            # Check session sebelum mulai channel baru
            if not await scraper.check_session():
                scraper.log("[!] Session lost before channel - re-logging in")
                if not await scraper.re_login_and_resume(channel["value"], 1, start_date, end_date):
                    scraper.log("[-] Failed to resume, skipping channel")
                    continue
            else:
                # Session OK, select channel normally
                if not await scraper.select_channel(channel["value"]):
                    scraper.log(f"[-] Failed to select channel {channel['label']}")
                    continue

                if not await scraper.set_date_range(start_date, end_date):
                    scraper.log(
                        f"[-] Failed to set date range untuk channel {channel['label']}"
                    )
                    continue

                if not await scraper.query_playback():
                    scraper.log(
                        f"[-] Failed to query playback untuk channel {channel['label']}"
                    )
                    continue

            # Get pagination info
            page_info = await scraper.get_pagination_info()

            for page in range(1, page_info["total"] + 1):
                # Set current page context
                scraper.current_page = page

                # Check statistik page ini
                page_stats = scraper.get_page_stats(channel["value"], page)
                if page_stats["downloaded_count"] > 0:
                    scraper.log(
                        f"[*] Page {page} - Already downloaded {page_stats['downloaded_count']} files before"
                    )

                # Check session sebelum setiap page
                if not await scraper.check_session():
                    scraper.log(f"[!] Session lost at page {page} - re-logging in")
                    if not await scraper.re_login_and_resume(channel["value"], page, start_date, end_date):
                        scraper.log(f"[-] Failed to resume to page {page}, skipping rest of channel")
                        break
                else:
                    # Session OK, navigate page normally (if not page 1)
                    if page > 1:
                        scraper.log(f"[*] Navigasi ke halaman {page}...")
                        await scraper.page.evaluate(
                            f"""(targetPage) => {{
                            const input = document.getElementById('playback_jump_page');
                            const btn = document.getElementById('playback_jump');
                            if (input && btn) {{
                                input.value = targetPage;
                                btn.click();
                            }}
                        }}""",
                            page,
                        )
                        await asyncio.sleep(2)

                # Extract table data
                table_data = await scraper.extract_table_data()
                if table_data:
                    scraper.log(
                        f"\n[+] Found {len(table_data)} files on page {page} of {page_info['total']}"
                    )
                    if page == 1:
                        scraper.log("[*] Sample files:")
                        for idx, file in enumerate(table_data[:3]):
                            scraper.log(
                                f"    {idx + 1}. {file['startTime']} -> {file['endTime']} ({file['type']})"
                            )
                else:
                    scraper.log(
                        f"[*] No files found on page {page} of {page_info['total']}"
                    )
                    continue

                # Retry logic: max 3 attempts per page
                max_retries = 3
                retry_count = 0

                while retry_count < max_retries:
                    # Check session sebelum retry
                    if not await scraper.check_session():
                        scraper.log(f"[!] Session lost during page {page} processing - re-logging in")
                        if not await scraper.re_login_and_resume(channel["value"], page, start_date, end_date):
                            scraper.log(f"[-] Failed to resume, aborting page {page}")
                            break

                    if retry_count > 0:
                        scraper.log(
                            f"\n[RETRY {retry_count + 1}/{max_retries}] Page {page} - Re-downloading failed files"
                        )
                        scraper.log("[*] System will auto-skip files already in database")
                        # Delay lebih lama untuk retry (exponential backoff)
                        retry_delay = 5 * (2 ** (retry_count - 1))  # 5s, 10s, 20s
                        await asyncio.sleep(retry_delay)

                    # Select all and download
                    if retry_count == 0:
                        scraper.log(f"\n[*] Download semua file di halaman {page}...")

                    if not await scraper.select_all_files():
                        scraper.log("[-] Failed to select all files")
                        break

                    if not await scraper.start_download():
                        scraper.log("[-] Failed to start download")
                        break

                    # Wait for download completion (10 minutes per page)
                    download_result = await scraper.wait_for_download_completion(600000)

                    if not download_result["completed"]:
                        scraper.log(
                            f"[!] Download halaman {page} tidak selesai dalam timeout"
                        )
                        break

                    # Check if there are failures
                    if download_result["failure"] > 0:
                        scraper.log(
                            f"[!] Page {page} has {download_result['failure']} failed files"
                        )
                        retry_count += 1

                        if retry_count < max_retries:
                            scraper.log(f"[*] Will retry (attempt {retry_count + 1}/{max_retries})...")
                            continue
                        else:
                            scraper.log(
                                f"[!] Max retries reached, skipping remaining failures"
                            )
                            break
                    else:
                        # All files succeeded
                        scraper.log(
                            f"[✓] Page {page} completed successfully - all {download_result['success']} files downloaded"
                        )
                        break

                # Files are already organized in real-time by _handle_download
                stats = scraper.get_download_stats()
                scraper.log(f"[*] Stats - Session: {stats['completed']}, Total ever: {stats['total_ever']}")

            scraper.log(f"\n[+] Channel {channel['label']} selesai")

        scraper.log("\n[+] Download flow untuk semua channel selesai")
    except Exception as error:
        scraper.log(f"[-] Error in DownloadPlayback: {str(error)}")


async def main():
    """Main entry point"""
    scraper = DeviceScraper("192.168.88.19")

    # Setup signal handler for graceful shutdown
    def signal_handler(sig, frame):
        scraper.log("[!] SIGINT (CTRL+C) diterima, menutup browser...")
        asyncio.create_task(scraper.close())
        sys.exit(130)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        await scraper.initialize()

        if await scraper.login("scrapper", "sc@10001"):
            scraper.log("[+] Authenticated")
            await scraper.click_xpath('//*[@id="main_playback"]')
            await asyncio.sleep(1.5)
            await scraper.click_xpath('//*[@id="playback_new_download"]')
            scraper.log("[*] Entering playback menu...")
            await asyncio.sleep(1.5)

            await download_playback(scraper)
        else:
            scraper.log("[-] Authentication failed")
    except Exception as error:
        scraper.log(f"[-] Unexpected error: {str(error)}")
    finally:
        await scraper.close()


if __name__ == "__main__":
    asyncio.run(main())
