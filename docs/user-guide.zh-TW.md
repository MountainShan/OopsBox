# OopsBox 使用說明

[English](user-guide.md)

這份說明適用於已經安裝好的 OopsBox。安裝方式請看 [README](../README.md#安裝)。介面是英文的，所以按鈕和選單名稱都保留原文。

- [登入](#登入)
- [專案](#專案)
- [選擇專案要用的 agent](#選擇專案要用的-agent)
- [和 agent 對話](#和-agent-對話)
- [agent 需要你的時候](#agent-需要你的時候)
- [終端機](#終端機)
- [檔案](#檔案)
- [狀態列](#狀態列)
- [設定](#設定)
- [手機和 iPad](#手機和-ipad)
- [實驗性 agent](#實驗性-agent)
- [疑難排解](#疑難排解)

## 登入

用瀏覽器打開 OopsBox，輸入安裝時設定的帳號密碼。之後可以在 **Settings → Login Credentials** 修改。

有 HTTPS 網址的話請用 HTTPS，例如透過 `tailscale serve` 提供的 `https://<機器>.<tailnet>.ts.net:9444`。推播通知和安裝成 App 都只能在 HTTPS 下使用。

## 專案

**Projects** 分頁（☰，固定是第一個分頁）每個專案一張卡片。

| 卡片上的東西 | 意思 |
|---|---|
| 名稱前的圓點 | 綠色：這個專案有東西在跑 |
| `local` / `ssh` | 專案的檔案和 agent 在哪台機器上 |
| 分級選單 | `personal`、`work` 或 `restricted`，決定專案可以用哪些 agent（見下方） |
| agent 標籤 | 目前在跑的 agent。工作中會轉圈；在等你時變成黃色的 **needs you** |
| **Open** | 在獨立分頁打開專案 |
| **Start / Stop** | 啟動專案的 agent，或停掉 agent 和終端機 |
| **Agents** | 選擇專案要用哪些 agent |
| **Edit** | SSH 專案的連線設定 |
| **Delete** | 從 OopsBox 移除專案 |

### 建立專案

按 **+ New Project**，填入：

- **Project Name**：英數字、`.`、`_`、`-`。
- **Type**：
  - **Local**：專案放在 OopsBox 伺服器上的 `~/projects/<名稱>`。
  - **SSH Remote**：agent 和終端機在另一台機器的 **Remote Path** 裡工作。填密碼或金鑰路徑；兩個都留空則使用伺服器本身的 SSH 金鑰。
- **Classification** 和 **Agents**：見下一節。

如果 `~/projects/<名稱>` 已經存在（例如之前移除過的專案），OopsBox 會直接沿用，不會改動裡面的任何東西。

### 分級

分級是上限：決定哪些 agent 可以在這個專案工作。

| 分級 | Claude、Codex | Kiro | 實驗性 agent |
|---|---|---|---|
| `personal` | 可用 | 可用，預設不勾 | 可用 |
| `work` | 可用 | 可用 | 可用 |
| `restricted` | 封鎖 | 可用 | 封鎖 |

改成較嚴格的分級時，被封鎖的 agent 會立刻停止。不能送到雲端 agent 的程式碼，請用 `restricted`。

### 刪除專案

**Delete** 只是把專案從 OopsBox 移除，資料夾和對話記錄都會保留；之後建立同名專案，一切都會回來。要連資料夾和對話記錄一起刪，勾選 **Also delete the folder and the project's chat history** 並輸入專案名稱確認。這個動作無法復原。

## 選擇專案要用的 agent

![新增專案對話框中的 agent 選擇](../images/new-project.png)

每個專案只使用你勾選的 agent，順序就是勾選清單的順序。打開專案時預設是第一個，直到你改用別的。

- **建立專案時**，清單會列出分級允許的所有 agent，並顯示 OopsBox 在那台機器上找到的狀況：
  - 顯示版本（例如 `codex-cli 0.164.0`）：已安裝，預設勾選；
  - 黃字 **Not installed here** 加上安裝方式：預設不勾，但仍然可以勾（例如你正準備安裝）；
  - **Not checked on this machine yet**：OopsBox 還沒檢查過那台機器（新的 SSH 主機會在專案建立後檢查）。
- **之後要修改**，按專案卡片上的 **Agents**。取消勾選正在跑的 agent 會讓它停止；對話記錄會保留，重新勾選後就會回來。
- 分級封鎖的 agent 不會出現在清單裡，不管怎麼勾都不能用。

在這個功能推出前建立的專案，會繼續使用分級允許的全部 agent，直到你修改為止。

### 每台機器裝了哪些 agent

![設定頁的 Agents 區塊](../images/agents-settings.png)

**Settings → Agents** 列出 OopsBox 伺服器和專案用到的每台 SSH 主機上，裝了哪些 agent、版本和路徑。OopsBox 啟動時會檢查一次，新增一個新 SSH 主機上的專案時也會檢查。安裝或更新 agent 之後，請按 **Re-detect**。15 秒內沒有回應的主機會顯示 **timed out**。

## 和 agent 對話

打開專案，從聊天畫面上方的列選擇 agent。每個 agent 都有自己的顏色：Claude 橘色、Codex 綠色、Kiro 紫色，實驗性 agent 是灰色並帶有 **exp** 標記。

- **送出**：Enter。Shift+Enter 換行。
- **agent 工作中**：再傳一則訊息可以調整目前這一輪的方向，或按 **Interrupt** 中斷。⏻ 會停止 agent。
- **斜線指令**：輸入 `/` 會列出 agent 的指令。有些會打開選單（`/model`）或面板（`/usage`、`/context`、`/mcp`）；只能在 agent 自己介面裡用的指令，會建議你改到終端機繼續。
- **模型和模式**：從聊天列的選單選擇。
- **權限**：agent 請求權限時會出現 Allow / Deny 卡片。**Autopilot** 會在這段對話中自動允許所有請求。
- **檔案和圖片**：用 📎 附加、拖進聊天框，或直接貼上圖片。檔案會存到專案的 `uploads/` 資料夾。輸入 `@` 可以搜尋專案裡的檔案。
- **New chat** 開始新的對話。🕘 列出過去的對話（也包括其他 agent 的記錄），可以切回去。
- **Open in Terminal** 會在 agent 自己的 CLI 裡繼續同一段對話，處理聊天介面做不到的事。

關掉網頁後 agent 會繼續跑。OopsBox 重新啟動時，原本在跑的 agent 會回到原來的對話。你手動停止的 agent 會保持停止。

## agent 需要你的時候

agent 停下來等你批准權限或回答問題時：

- 專案卡片上它的標籤會變黃（**needs you**）；
- 每個頁面的狀態列都會顯示 **⚠ \<agent\> needs you · \<專案\>**，點一下就會跳過去；
- 瀏覽器分頁標題會顯示數量；
- 每台開啟通知的裝置都會收到推播，點推播會直接打開那段對話。

開啟通知的方式：在每台裝置上打開 **Settings → Notifications**，按 **Turn on for this device**，再按 **Send a test** 確認。iPad 或 iPhone 要先用 Safari 把 OopsBox 加入主畫面（分享 → 加入主畫面），並從主畫面打開；iOS 只會把網頁推播送給已安裝的 App。

## 終端機

- 專案裡的 **Terminal** 會在專案資料夾開一個 shell（SSH 專案則是在遠端主機上）。
- 側邊欄的 **›_** 會在新分頁開一個獨立的 shell，再按一次就再開一個。
- 關掉網頁或 OopsBox 重新啟動，終端機都會繼續跑。關掉獨立 shell 的分頁就會結束它。
- 在觸控螢幕上，工具列提供鍵盤才有的按鍵：^C、Tab、方向鍵、Esc。

## 檔案

**Files** 分頁可以瀏覽專案：上傳、下載、重新命名、刪除，用編輯器打開程式碼，或預覽 Markdown、圖片和 PDF。SSH 專案會把遠端檔案和本機檔案並排顯示。

## 狀態列

底部的狀態列顯示每個 agent 的用量（每個額度用了多少），以及機器的 CPU、記憶體、磁碟和 GPU。點一個 agent 可以看細節和額度重置時間。手機上顯示的比較少，點 agent 可以看其餘部分。

額度超過警示門檻時會收到推播，每個重置週期只通知一次。門檻在 **Settings → Notifications** 設定（預設 90%）。

## 設定

側邊欄的 ⚙ 打開設定：

| 區塊 | 用途 |
|---|---|
| Login Credentials | 帳號和密碼 |
| Claude Agent | Anthropic API key 和 base URL，是舊版留下的設定；聊天裡的 agent 使用它們自己的登入 |
| Git | 伺服器上的 git 使用者名稱和 email |
| Notifications | 這台裝置的推播、測試通知、用量警示門檻 |
| Agents | 每台機器裝了哪些 agent；Re-detect |
| Uploads | 上傳檔案的大小上限，以及聊天附件存放的資料夾 |
| SSL | 由 OopsBox 自己提供 HTTPS 時的憑證路徑 |

## 手機和 iPad

OopsBox 可以直接在手機瀏覽器使用。建議安裝成 App：Safari → 分享 → **加入主畫面**，或在 Chrome 選單選 **安裝**。在手機上，聊天列會換成兩行，專案分頁縮成 ☰，放不下的分頁收在 **▾ N** 裡。

## 實驗性 agent

除了 Claude Code、Codex 和 Kiro，只要機器上有安裝，OopsBox 也能執行這些 agent：

| Agent | 啟動方式 |
|---|---|
| Gemini CLI | `gemini --acp` |
| Goose | `goose acp` |
| OpenCode | `opencode acp` |

它們都支援 ACP（Agent Client Protocol），所以 OopsBox 用跟 Kiro 同一個 driver 執行它們。它們目前是**實驗性**的：還沒有人確認過聊天介面能提供它們自己 CLI 的所有功能。具體來說：

- 永遠不會預設勾選，`restricted` 專案也不能使用；
- 狀態列沒有它們的用量；
- 斜線指令會直接當成文字送給 agent，沒有選單或面板；
- 沒有 **Open in Terminal**。

想試用的話：在機器上安裝並在終端機裡登入，到 Settings → Agents 按 **Re-detect**，再到專案的 **Agents** 勾選它。

## 疑難排解

| 問題 | 處理方式 |
|---|---|
| agent 啟動後一直顯示錯誤 | 開終端機直接執行那個 agent：通常是還沒登入，或那台機器沒裝（看 Settings → Agents） |
| 聊天畫面不再更新 | 重新整理頁面。還是不更新的話，確認伺服器連得上；伺服器恢復後 OopsBox 會自己重新連線 |
| 收不到推播 | 使用 HTTPS 網址；iOS 要從主畫面打開；在 Settings → Notifications 按 **Send a test** |
| SSH 主機顯示 **timed out** | 用 **Edit** 檢查專案的連線設定，再按 **Re-detect** |
| 明明裝了卻顯示 "Not installed here" | agent 必須在 OopsBox 使用的 PATH 上（`~/.local/bin`、`~/.npm-global/bin` 和系統 PATH）；然後按 **Re-detect** |
