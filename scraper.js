const puppeteer = require("puppeteer");

// Handler SIGINT agar CTRL+C langsung menutup browser dan exit
process.on("SIGINT", async () => {
  log("[!] SIGINT (CTRL+C) diterima, menutup browser...");
  if (globalThis.scraper && globalThis.scraper.close)
    await globalThis.scraper.close();
  process.exit(130); // 130 = 128 + SIGINT
});
const readline = require("readline");
const fs = require("fs");
const path = require("path");

// === LOGGING ===
// Konfigurasi path log (lintas platform, bisa diubah)
const LOG_DIR = path.join(__dirname, "downloads");
const LOG_FILE = path.join(LOG_DIR, "log.txt");

// Otomatis buat folder log jika belum ada
if (!fs.existsSync(LOG_DIR)) fs.mkdirSync(LOG_DIR, { recursive: true });

// Aktifkan log ke file (atau matikan untuk log ke console saja)
let logStream = fs.createWriteStream(LOG_FILE, { flags: "a" });

function log(msg) {
  if (logStream) logStream.write(msg + "\n");
  else console.log(msg);
}

// Contoh hasil:
//   - Windows: D:\Documents\PlaybackDownloader\downloads\log.txt
//   - Linux:   /home/username/PlaybackDownloader/downloads/log.txt

const rl = readline.createInterface({
  input: process.stdin,

  output: process.stdout,
});

class DeviceScraper {
  constructor(host = "192.168.88.19") {
    this.baseUrl = `http://${host}`;
    this.browser = null;
    this.page = null;
    this.cookiePath = path.join(__dirname, "cookies.json");
    this.storagePath = path.join(__dirname, "storage.json");
    this.downloadDir = path.join(__dirname, "downloads");
    this.organizedDir = path.join(__dirname, "downloads", "cctv");
  }

  async saveCookies() {
    const cookies = await this.page.cookies();
    fs.writeFileSync(this.cookiePath, JSON.stringify(cookies, null, 2));
    log("[+] Cookies saved");
  }

  async loadCookies() {
    if (fs.existsSync(this.cookiePath)) {
      const cookies = JSON.parse(fs.readFileSync(this.cookiePath, "utf-8"));
      await this.page.setCookie(...cookies);
      log("[+] Cookies loaded");
      return true;
    }
    return false;
  }

  async saveLocalStorage() {
    const storage = await this.page.evaluate(() =>
      JSON.stringify(localStorage),
    );
    fs.writeFileSync(this.storagePath, storage);
    log("[+] LocalStorage saved");
  }

  async loadLocalStorage() {
    if (fs.existsSync(this.storagePath)) {
      const storage = fs.readFileSync(this.storagePath, "utf-8");
      await this.page.evaluate((data) => {
        Object.entries(JSON.parse(data)).forEach(([key, value]) => {
          localStorage.setItem(key, value);
        });
      }, storage);
      log("[+] LocalStorage loaded");
      return true;
    }
    return false;
  }

  async initialize() {
    log("[*] Launching browser...");

    // Create downloads directory if it doesn't exist
    if (!fs.existsSync(this.downloadDir)) {
      fs.mkdirSync(this.downloadDir, { recursive: true });
    }

    this.browser = await puppeteer.launch({
      headless: true,
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        `--download-path=${this.downloadDir}`,
      ],
    });

    const pages = await this.browser.pages();
    this.page = pages[0];

    // Setup download path for CDP
    const client = await this.page.target().createCDPSession();
    await client.send("Page.setDownloadBehavior", {
      behavior: "allow",
      downloadPath: this.downloadDir,
    });

    log(`[+] Browser ready, downloads to: ${this.downloadDir}`);
  }

  async login(username = "scrapper", password = "sc@10001") {
    try {
      const loginUrl = `${this.baseUrl}/`;

      log(`[*] Navigating to ${loginUrl}`);
      await this.page.goto(loginUrl, { timeout: 30000 });
      await this.page.reload({
        waitUntil: ["domcontentloaded", "networkidle2"],
      });
      // Tunggu sampai URL ada #login dan networkidle
      await this.page.waitForFunction(
        () => window.location.hash.includes("login"),
        { timeout: 15000 },
      );
      await this.page.evaluate(() => new Promise((r) => setTimeout(r, 1000)));

      log("[*] Waiting for login form...");
      await this.page.waitForSelector("#login_u", { timeout: 15000 });
      log("[+] Login form found");

      log("[*] Pause 2 detik - tunggu form siap...");
      await this.page.evaluate(() => new Promise((r) => setTimeout(r, 2000)));

      log(`[*] Entering username: ${username}`);
      await this.page.type("#login_u", username, { delay: 100 });
      await this.page.evaluate(() => new Promise((r) => setTimeout(r, 200)));

      log(`[*] Entering password: ${"*".repeat(password.length)}`);
      await this.page.click("#login_p");
      await this.page.evaluate(() => new Promise((r) => setTimeout(r, 500)));
      await this.page.type("#login_p", password, { delay: 100 });
      await this.page.evaluate(() => new Promise((r) => setTimeout(r, 1000)));
      // console.log(
      //   "[*] Input form selesai. Tekan Enter untuk klik login button...",
      // );
      // await pause();

      log("[*] Clicking login button...");
      await this.page.click("#login_s");

      // Tunggu redirect sampai URL tidak ada #login
      log("[*] Waiting for redirect...");
      await this.page.waitForFunction(
        () => !window.location.hash.includes("login"),
        { timeout: 10000 },
      );
      await this.page.evaluate(() => new Promise((r) => setTimeout(r, 1000)));

      const currentUrl = this.page.url();
      log(`[*] Current URL: ${currentUrl}`);

      // Verify dengan cek element #main_user_logo
      try {
        await this.page.waitForSelector("#main_user_logo", { timeout: 5000 });
        log("[+] Login berhasil!");
        await this.page.evaluate(() => new Promise((r) => setTimeout(r, 1500)));
        return true;
      } catch {
        log("[-] Login gagal - element login tidak ditemukan");
        return false;
      }
    } catch (error) {
      log(`[-] Error: ${error.message}`);
      return false;
    }
  }

  async getPageSource() {
    return await this.page.content();
  }

  async extractXPath(xpath) {
    try {
      const elements = await this.page.$x(xpath);
      return elements;
    } catch (error) {
      log(`[-] Error extracting XPath: ${error.message}`);
      return null;
    }
  }

  async clickXPath(xpath) {
    try {
      await this.page.evaluate((xp) => {
        const element = document.evaluate(
          xp,
          document,
          null,
          XPathResult.FIRST_ORDERED_NODE_TYPE,
          null,
        ).singleNodeValue;
        if (element) {
          element.click();
        }
      }, xpath);
      console.log(`[+] Clicked element: ${xpath}`);
      return true;
    } catch (error) {
      log(`[-] Error clicking XPath: ${error.message}`);
      return false;
    }
  }

  async getChannelList() {
    try {
      log("[*] Extracting channel list...");
      const channels = await this.page.evaluate(() => {
        const select = document.getElementById("playback_down_channel");
        if (!select) return [];
        const options = Array.from(select.options);
        return options.map((opt) => ({
          value: parseInt(opt.value),
          label: opt.textContent.trim(),
        }));
      });
      log(`[+] Found ${channels.length} channels`);
      return channels;
    } catch (error) {
      log(`[-] Error extracting channels: ${error.message}`);
      return [];
    }
  }

  async selectChannel(channelValue) {
    try {
      await this.page.select("#playback_down_channel", channelValue.toString());
      log(`[+] Selected channel value: ${channelValue}`);
      await this.page.evaluate(() => new Promise((r) => setTimeout(r, 500)));
      return true;
    } catch (error) {
      log(`[-] Error selecting channel: ${error.message}`);
      return false;
    }
  }

  async setDateRange(startDate, endDate) {
    try {
      log(`[*] Setting date range: ${startDate} to ${endDate}`);

      // Set start date
      const startElement = await this.page.$("#playback_down_start");
      if (startElement) {
        await this.page.evaluate(() => {
          const elem = document.getElementById("playback_down_start");
          elem.value = "";
          elem.focus();
        });
        await this.page.type("#playback_down_start", startDate, { delay: 50 });
      }

      await this.page.evaluate(() => new Promise((r) => setTimeout(r, 300)));

      // Set end date
      const endElement = await this.page.$("#playback_down_end");
      if (endElement) {
        await this.page.evaluate(() => {
          const elem = document.getElementById("playback_down_end");
          elem.value = "";
          elem.focus();
        });
        await this.page.type("#playback_down_end", endDate, { delay: 50 });
      }

      await this.page.evaluate(() => new Promise((r) => setTimeout(r, 300)));
      log("[+] Date range set");
      return true;
    } catch (error) {
      log(`[-] Error setting date range: ${error.message}`);
      return false;
    }
  }

  async queryPlayback() {
    try {
      log("[*] Querying playback...");
      await this.clickXPath('//*[@id="playback_down_query"]');

      // Tunggu table loading
      await this.page.evaluate(() => new Promise((r) => setTimeout(r, 2000)));

      // Cek apakah ada hasil (div-based table)
      const tableExists = await this.page.evaluate(() => {
        const table = document.querySelector("div.td-table-body");
        return table ? true : false;
      });

      if (!tableExists) {
        console.log("[-] No results table found");
        return [];
      }

      log("[+] Query completed");
      return true;
    } catch (error) {
      log(`[-] Error querying playback: ${error.message}`);
      return false;
    }
  }

  async extractTableData() {
    try {
      log("[*] Extracting table data...");

      const tableData = await this.page.evaluate(() => {
        // Use div-based table selectors
        const rows = Array.from(
          document.querySelectorAll("div.td-table-body div.td-table-row"),
        );
        return rows.map((row) => {
          const cells = Array.from(row.querySelectorAll("span.td-table-cell"));
          // cells[0] = checkbox, cells[1] = channel, cells[2] = start, cells[3] = end, cells[4] = type, cells[5] = lock
          return {
            channel: cells[1]?.textContent?.trim() || "",
            startTime: cells[2]?.textContent?.trim() || "",
            endTime: cells[3]?.textContent?.trim() || "",
            type: cells[4]?.textContent?.trim() || "",
            lock: cells[5]?.textContent?.trim() || "",
          };
        });
      });

      log(`[+] Extracted ${tableData.length} files from table`);
      return tableData;
    } catch (error) {
      log(`[-] Error extracting table data: ${error.message}`);
      return [];
    }
  }

  async getPaginationInfo() {
    try {
      const pageInfo = await this.page.evaluate(() => {
        const currentPageElem = document.getElementById("playback_pagecur");
        const totalPageElem = document.getElementById("playback_pagecount");

        return {
          current: currentPageElem ? parseInt(currentPageElem.textContent) : 1,
          total: totalPageElem ? parseInt(totalPageElem.textContent) : 1,
        };
      });

      log(`[*] Pagination: Page ${pageInfo.current} of ${pageInfo.total}`);
      return pageInfo;
    } catch (error) {
      log(`[-] Error getting pagination info: ${error.message}`);
      return { current: 1, total: 1 };
    }
  }

  async selectAllFiles() {
    try {
      log("[*] Selecting all files on this page...");

      // Click checkbox input in td-table-header
      const checkboxClicked = await this.page.evaluate(() => {
        const checkbox = document.querySelector(
          "div.td-table-header input.checkbox",
        );
        if (checkbox) {
          checkbox.click();
          return true;
        }
        return false;
      });

      if (!checkboxClicked) {
        log("[-] Checkbox not found");
        return false;
      }

      await this.page.evaluate(() => new Promise((r) => setTimeout(r, 500)));
      log("[+] All files selected");
      return true;
    } catch (error) {
      log(`[-] Error selecting all files: ${error.message}`);
      return false;
    }
  }

  async startDownload() {
    try {
      log("[*] Starting download...");
      await this.clickXPath('//*[@id="playback_start_download"]');
      await this.page.evaluate(() => new Promise((r) => setTimeout(r, 1000)));
      log("[+] Download button clicked");
      return true;
    } catch (error) {
      log(`[-] Error starting download: ${error.message}`);
      return false;
    }
  }

  async waitForDownloadCompletion(timeoutMs = 600000) {
    try {
      log("[*] Waiting for download to complete...");
      const startTime = Date.now();

      // Wait initial delay for dialog to appear
      await this.page.evaluate(() => new Promise((r) => setTimeout(r, 2000)));

      let lastProgress = "";
      let noChangeCount = 0;
      let lastProgressTime = Date.now();

      // Poll for download completion - monitor progress info and alert
      while (Date.now() - startTime < timeoutMs) {
        const status = await this.page.evaluate(() => {
          const infoElem = document.getElementById("playback_down_info");
          const stopBtn = document.getElementById("playback_down_stop");
          const progressBar = document.getElementById("playback_down_progress");
          const alertElem = document.getElementById("info_");
          const showbox = document.getElementById("showbox");

          return {
            infoText: infoElem ? infoElem.textContent.trim() : null,
            stopBtnExists: stopBtn ? true : false,
            progressWidth: progressBar ? progressBar.style.width : null,
            alertVisible: alertElem
              ? alertElem.style.display !== "none"
              : false,
            alertText: showbox ? showbox.textContent.trim() : null,
          };
        });

        // Check if success alert appeared
        if (status.alertVisible && status.alertText) {
          log(`[+] Download alert: ${status.alertText}`);

          // Parse result: "Download Result: Success X, Failure Y"
          const successMatch = status.alertText.match(/Success (\d+)/);
          const failureMatch = status.alertText.match(/Failure (\d+)/);

          if (successMatch && failureMatch) {
            const success = parseInt(successMatch[1]);
            const failure = parseInt(failureMatch[1]);
            log(
              `[+] Download completed: ${success} success, ${failure} failure`,
            );
            return true;
          }
        }

        // Check progress text
        if (status.infoText) {
          // Log any change in progress
          if (status.infoText !== lastProgress) {
            log(`[*] ${status.infoText}`);
            lastProgress = status.infoText;
            noChangeCount = 0;
            lastProgressTime = Date.now();
          } else {
            noChangeCount++;
          }

          // Parse progress: "Downloading…(5/20)" or similar
          const match = status.infoText.match(/\((\d+)\/(\d+)\)/);
          if (match) {
            const current = parseInt(match[1]);
            const total = parseInt(match[2]);

            // Check if all files downloaded
            if (current === total && total > 0) {
              log(`[+] All files queued for download (${current}/${total})`);
              // Wait for alert to appear
              let waitCount = 0;
              while (waitCount < 60) {
                const alertShowing = await this.page.evaluate(() => {
                  const alert = document.getElementById("info_");
                  return alert ? alert.style.display !== "none" : false;
                });

                if (alertShowing) {
                  // Alert appeared, wait a moment for full render
                  await this.page.evaluate(
                    () => new Promise((r) => setTimeout(r, 1000)),
                  );
                  const resultText = await this.page.evaluate(() => {
                    const showbox = document.getElementById("showbox");
                    return showbox ? showbox.textContent.trim() : null;
                  });

                  log(`[+] ${resultText}`);
                  return true;
                }

                await this.page.evaluate(
                  () => new Promise((r) => setTimeout(r, 1000)),
                );
                waitCount++;
              }

              log("[+] Download finished (timeout waiting for alert)");
              return true;
            }
          }

          // If progress hasn't changed for 60+ seconds, might be complete
          const timeSinceLastChange = Date.now() - lastProgressTime;
          if (
            noChangeCount > 30 &&
            timeSinceLastChange > 60000 &&
            !status.stopBtnExists
          ) {
            log(
              "[!] Download progress unchanged for 60s and button gone, completing",
            );
            return true;
          }
        }

        // If stop button disappeared, download is done
        if (!status.stopBtnExists && status.infoText === null) {
          log("[+] Download completed (stop button disappeared)");
          return true;
        }

        await this.page.evaluate(() => new Promise((r) => setTimeout(r, 2000)));
      }

      log("[-] Download timeout");
      return false;
    } catch (error) {
      log(`[-] Error waiting for download completion: ${error.message}`);
      return false;
    }
  }

  async close() {
    if (this.browser) {
      await this.browser.close();
      log("[+] Browser closed");
    }
  }
}
async function DownloadPlayback(scraper) {
  try {
    log("\n[*] === Starting Download Playback Flow ===\n");

    // Get all channels
    const channels = await scraper.getChannelList();
    if (channels.length === 0) {
      log("[-] No channels found");
      return;
    }

    // Filter to only channels [1] through [21]
    const activeChannels = channels.filter((ch) => {
      const match = ch.label.match(/^\[(\d+)\]/);
      if (!match) return false;
      const num = parseInt(match[1]);
      return num >= 1 && num <= 21;
    });
    log(
      `[+] Active channels: ${activeChannels.length} (filtered from ${channels.length})`,
    );
    activeChannels.slice(0, 3).forEach((ch) => {
      log(`    - Value: ${ch.value}, Label: ${ch.label}`);
    });

    // Set date range - use format: YYYY-MM-DD HH:MM:SS
    // Start from yesterday 24 hours
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const dateStr = yesterday.toISOString().split("T")[0];
    const startDate = `${dateStr} 00:00:00`;
    const endDate = `${dateStr} 23:59:59`;

    // Loop untuk semua channel aktif
    for (const channel of activeChannels) {
      log(`\n[*] Memproses channel: ${channel.label}`);

      if (!(await scraper.selectChannel(channel.value))) {
        log(`[-] Failed to select channel ${channel.label}`);
        continue;
      }

      if (!(await scraper.setDateRange(startDate, endDate))) {
        log(`[-] Failed to set date range untuk channel ${channel.label}`);
        continue;
      }

      if (!(await scraper.queryPlayback())) {
        log(`[-] Failed to query playback untuk channel ${channel.label}`);
        continue;
      }

      // Get pagination info
      let pageInfo = await scraper.getPaginationInfo();
      let allTableData = [];

      for (let page = 1; page <= pageInfo.total; page++) {
        if (page > 1) {
          // Pindah ke halaman berikutnya
          console.log(`[*] Navigasi ke halaman ${page}...`);
          await scraper.page.evaluate((targetPage) => {
            const input = document.getElementById("playback_jump_page");
            const btn = document.getElementById("playback_jump");
            if (input && btn) {
              input.value = targetPage;
              btn.click();
            }
          }, page);
          // Tunggu halaman update
          await new Promise((r) => setTimeout(r, 2000));
        }

        // Ambil data tabel halaman ini
        const tableData = await scraper.extractTableData();
        if (tableData.length > 0) {
          log(
            `\n[+] Found ${tableData.length} files on page ${page} of ${pageInfo.total}`,
          );
          if (page === 1) {
            log("[*] Sample files:");
            tableData.slice(0, 3).forEach((file, idx) => {
              log(
                `    ${idx + 1}. ${file.startTime} -> ${file.endTime} (${file.type})`,
              );
            });
          }
        } else {
          log(`[*] No files found on page ${page} of ${pageInfo.total}`);
          continue;
        }

        // Select all dan download
        log(`\n[*] Download semua file di halaman ${page}...`);
        if (!(await scraper.selectAllFiles())) {
          log("[-] Failed to select all files");
          continue;
        }
        if (!(await scraper.startDownload())) {
          log("[-] Failed to start download");
          continue;
        }
        // Tunggu download selesai (10 menit per halaman)
        const downloadCompleted =
          await scraper.waitForDownloadCompletion(600000);
        if (!downloadCompleted) {
          log(`[!] Download halaman ${page} tidak selesai dalam timeout`);
        }

        // Gabung data tabel untuk organisasi file
        allTableData = allTableData.concat(tableData);

        // Organisasi file setelah setiap halaman
        if (downloadCompleted) {
          log("\n[*] Organizing downloaded files...");

          const downloadsPath = scraper.downloadDir;
          const organizedBase = scraper.organizedDir;
          const allItems = fs.readdirSync(downloadsPath);
          const mp4Files = allItems.filter((item) => {
            const itemPath = path.join(downloadsPath, item);
            return fs.statSync(itemPath).isFile() && item.endsWith(".mp4");
          });

          log(`[*] Found ${mp4Files.length} mp4 files to organize`);

          for (const mp4File of mp4Files) {
            try {
              const match = mp4File.match(
                /\d+\.\d+\.\d+\.\d+_(\d+)_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})/,
              );

              if (match) {
                const channelNum = parseInt(match[1]);
                const year = match[2];
                const month = match[3];
                const day = match[4];
                const hour = match[5];
                const minute = match[6];
                const second = match[7];

                const dateStr = `${year}-${month}-${day}`;

                // Cari data tabel yang cocok untuk end time
                const matchingRow = allTableData.find((row) => {
                  const rowStart = row.startTime.replace(/[-:\s]/g, "");
                  const fileStart = `${year}${month}${day}${hour}${minute}${second}`;
                  return rowStart.startsWith(fileStart.substring(0, 12));
                });

                let endHMS = "00:00:00";
                if (matchingRow) {
                  const endMatch = matchingRow.endTime.match(
                    /(\d{2}):(\d{2}):(\d{2})/,
                  );
                  if (endMatch) {
                    endHMS = `${endMatch[1]}:${endMatch[2]}:${endMatch[3]}`;
                  }
                }

                const channelFolder = path.join(
                  organizedBase,
                  `channel${channelNum}`,
                );
                if (!fs.existsSync(channelFolder)) {
                  fs.mkdirSync(channelFolder, { recursive: true });
                }

                const startFormatted = `${hour}-${minute}-${second}`;
                const endFormatted = endHMS.replace(/:/g, "-");
                const newFilename = `${dateStr}.${startFormatted}_${endFormatted}.mp4`;
                const oldPath = path.join(downloadsPath, mp4File);
                const newPath = path.join(channelFolder, newFilename);

                fs.renameSync(oldPath, newPath);
                log(`[+] Organized: ${mp4File} -> ${newFilename}`);
              } else {
                log(`[-] Could not parse filename: ${mp4File}`);
              }
            } catch (error) {
              log(`[-] Error organizing ${mp4File}: ${error.message}`);
            }
          }

          log("[+] Files organized");
        }
      }
    }

    log("\n[+] Download flow untuk semua channel selesai");
  } catch (error) {
    log(`[-] Error in DownloadPlayback: ${error.message}`);
  }
}

// Main
(async () => {
  const scraper = new DeviceScraper("192.168.88.19");
  globalThis.scraper = scraper;

  try {
    await scraper.initialize();

    if (await scraper.login("scrapper", "sc@10001")) {
      log("[+] Authenticated");
      await scraper.clickXPath('//*[@id="main_playback"]');
      await scraper.page.evaluate(
        () => new Promise((r) => setTimeout(r, 1500)),
      );
      await scraper.clickXPath('//*[@id="playback_new_download"]');
      log("[*] Entering playback menu...");
      await scraper.page.evaluate(
        () => new Promise((r) => setTimeout(r, 1500)),
      );

      await DownloadPlayback(scraper);
    } else {
      log("[-] Authentication failed");
    }
  } catch (error) {
    log(`[-] Unexpected error: ${error.message}`);
  } finally {
    await scraper.close();
    rl.close();
  }
})();
