import asyncio
import json
import os
import re
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

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

        # Download tracking
        self.pending_downloads = []
        self.completed_downloads = []

        # Create directories
        self.download_dir.mkdir(exist_ok=True)
        self.organized_dir.mkdir(exist_ok=True)

        # Setup logging
        self.log_stream = open(self.log_file, "a", encoding="utf-8")

    def log(self, msg: str):
        """Log message to file"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_msg = f"[{timestamp}] {msg}"
        self.log_stream.write(log_msg + "\n")
        self.log_stream.flush()
        print(log_msg)

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

    async def initialize(self):
        """Initialize browser and page"""
        self.log("[*] Launching browser...")

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
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

            self.log(f"[*] Downloading: {filename}")
            self.log(f"[*] Save path: {save_path}")

            # Save the download
            await download.save_as(str(save_path))

            # Verify file exists
            if save_path.exists():
                file_size = save_path.stat().st_size
                self.log(f"[+] File saved: {filename} ({file_size} bytes)")
            else:
                self.log(f"[-] WARNING: File not found after save: {save_path}")

            # Move to completed
            if download in self.pending_downloads:
                self.pending_downloads.remove(download)
            self.completed_downloads.append(filename)

            self.log(
                f"[+] Download completed: {filename} (Total: {len(self.completed_downloads)})"
            )

            # Organize file immediately (real-time)
            await self._organize_single_file(filename)

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

            # Parse filename: 192.168.88.19_1_20250129143000_20250129144500.mp4
            match = re.search(
                r"\d+\.\d+\.\d+\.\d+_(\d+)_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})",
                filename,
            )

            if not match:
                self.log(f"[-] Could not parse filename: {filename}")
                return

            channel_num = int(match.group(1))
            year = match.group(2)
            month = match.group(3)
            day = match.group(4)
            hour = match.group(5)
            minute = match.group(6)
            second = match.group(7)

            date_str = f"{year}-{month}-{day}"

            # Extract end time from filename if available
            end_match = re.search(
                r"_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\.mp4$", filename
            )
            end_hms = "00-00-00"
            if end_match:
                end_hms = (
                    f"{end_match.group(4)}-{end_match.group(5)}-{end_match.group(6)}"
                )

            # Create channel folder
            channel_folder = self.organized_dir / f"channel{channel_num}"
            channel_folder.mkdir(exist_ok=True)

            # Generate new filename
            start_formatted = f"{hour}-{minute}-{second}"
            new_filename = f"{date_str}.{start_formatted}_{end_hms}.mp4"

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

    async def login(
        self, username: str = "scrapper", password: str = "sc@10001"
    ) -> bool:
        """Login to device"""
        try:
            login_url = f"{self.base_url}/"

            self.log(f"[*] Navigating to {login_url}")
            await self.page.goto(login_url, timeout=30000)
            await self.page.reload(wait_until="networkidle")

            # Wait for login hash in URL
            await self.page.wait_for_function(
                "() => window.location.hash.includes('login')", timeout=15000
            )
            await asyncio.sleep(1)

            self.log("[*] Waiting for login form...")
            await self.page.wait_for_selector("#login_u", timeout=15000)
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

            # Wait for redirect
            self.log("[*] Waiting for redirect...")
            await self.page.wait_for_function(
                "() => !window.location.hash.includes('login')", timeout=10000
            )
            await asyncio.sleep(1)

            current_url = self.page.url
            self.log(f"[*] Current URL: {current_url}")

            # Verify login
            try:
                await self.page.wait_for_selector("#main_user_logo", timeout=5000)
                self.log("[+] Login berhasil!")
                await asyncio.sleep(1.5)
                return True
            except PlaywrightTimeout:
                self.log("[-] Login gagal - element login tidak ditemukan")
                return False

        except Exception as error:
            self.log(f"[-] Error: {str(error)}")
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

    async def start_download(self) -> bool:
        """Start download process"""
        try:
            self.log("[*] Starting download...")
            await self.click_xpath('//*[@id="playback_start_download"]')
            await asyncio.sleep(1)
            self.log("[+] Download button clicked")
            return True
        except Exception as error:
            self.log(f"[-] Error starting download: {str(error)}")
            return False

    async def wait_for_download_completion(self, timeout_ms: int = 600000) -> bool:
        """Wait for download to complete"""
        try:
            self.log("[*] Waiting for download to complete...")
            start_time = asyncio.get_event_loop().time()
            timeout_sec = timeout_ms / 1000

            # Reset download tracking for this batch
            initial_download_count = len(self.completed_downloads)

            # Wait initial delay
            await asyncio.sleep(2)

            last_progress = ""
            no_change_count = 0
            last_progress_time = asyncio.get_event_loop().time()
            expected_files = 0

            while asyncio.get_event_loop().time() - start_time < timeout_sec:
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
                        self.log(
                            f"[+] Download initiated: {success} success, {failure} failure"
                        )

                        # Wait a bit more for actual file downloads to complete
                        self.log("[*] Waiting for files to finish downloading...")
                        for wait_count in range(60):  # Wait up to 60 seconds
                            current_downloads = (
                                len(self.completed_downloads) - initial_download_count
                            )

                            if (
                                current_downloads >= expected_files
                                and len(self.pending_downloads) == 0
                            ):
                                self.log(
                                    f"[+] All {current_downloads} files downloaded successfully"
                                )
                                return True

                            if wait_count % 5 == 0:  # Log every 5 seconds
                                self.log(
                                    f"[*] Downloaded {current_downloads}/{expected_files} files, {len(self.pending_downloads)} pending"
                                )

                            await asyncio.sleep(1)

                        self.log(
                            f"[+] Download completed with {len(self.completed_downloads) - initial_download_count} files"
                        )
                        return True

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
                                    break

                                await asyncio.sleep(1)

                            # Now wait for actual downloads
                            self.log("[*] Waiting for actual file downloads...")
                            for wait_count in range(120):  # Wait up to 2 minutes
                                current_downloads = (
                                    len(self.completed_downloads)
                                    - initial_download_count
                                )

                                if (
                                    current_downloads >= expected_files
                                    and len(self.pending_downloads) == 0
                                ):
                                    self.log(
                                        f"[+] All {current_downloads} files downloaded successfully"
                                    )
                                    return True

                                if wait_count % 10 == 0:  # Log every 10 seconds
                                    self.log(
                                        f"[*] Downloaded {current_downloads}/{expected_files} files, {len(self.pending_downloads)} pending"
                                    )

                                await asyncio.sleep(1)

                            self.log(
                                f"[+] Download finished with {len(self.completed_downloads) - initial_download_count} files"
                            )
                            return True

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
                        return True

                # Check if stop button disappeared
                if not status["stopBtnExists"] and status["infoText"] is None:
                    self.log("[+] Download completed (stop button disappeared)")
                    # Still wait a bit for downloads
                    await asyncio.sleep(5)
                    return True

                await asyncio.sleep(2)

            self.log("[-] Download timeout")
            return False
        except Exception as error:
            self.log(f"[-] Error waiting for download completion: {str(error)}")
            return False

    async def close(self):
        """Close browser and cleanup"""
        if self.browser:
            await self.browser.close()
            self.log("[+] Browser closed")
        if self.playwright:
            await self.playwright.stop()
        if self.log_stream:
            self.log_stream.close()

    def get_download_stats(self):
        """Get current download statistics"""
        return {
            "completed": len(self.completed_downloads),
            "pending": len(self.pending_downloads),
            "files": self.completed_downloads,
        }

    def debug_download_dir(self):
        """Debug: list all files in download directory"""
        try:
            self.log(f"\n[DEBUG] Listing download directory: {self.download_dir}")
            all_files = list(self.download_dir.iterdir())
            self.log(f"[DEBUG] Total items in directory: {len(all_files)}")

            for item in all_files[:20]:  # Show first 20 items
                if item.is_file():
                    size = item.stat().st_size
                    self.log(f"[DEBUG]   FILE: {item.name} ({size} bytes)")
                elif item.is_dir():
                    self.log(f"[DEBUG]   DIR:  {item.name}/")
        except Exception as e:
            self.log(f"[-] Error listing directory: {str(e)}")


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

        # Loop through all active channels
        for channel in active_channels:
            scraper.log(f"\n[*] Memproses channel: {channel['label']}")

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
            all_table_data = []

            for page in range(1, page_info["total"] + 1):
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

                # Select all and download
                scraper.log(f"\n[*] Download semua file di halaman {page}...")
                if not await scraper.select_all_files():
                    scraper.log("[-] Failed to select all files")
                    continue

                if not await scraper.start_download():
                    scraper.log("[-] Failed to start download")
                    continue

                # Wait for download completion (10 minutes per page)
                download_completed = await scraper.wait_for_download_completion(600000)
                if not download_completed:
                    scraper.log(
                        f"[!] Download halaman {page} tidak selesai dalam timeout"
                    )

                # Merge table data (for reference)
                all_table_data.extend(table_data)

                # Files are already organized in real-time by _handle_download
                # Just log summary
                if download_completed:
                    scraper.log(
                        f"\n[✓] Page {page} completed - files auto-organized in real-time"
                    )
                    stats = scraper.get_download_stats()
                    scraper.log(
                        f"[*] Total files downloaded so far: {stats['completed']}"
                    )

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
