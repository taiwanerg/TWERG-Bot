import discord
from discord.ext import commands
from discord import app_commands

class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.current_page = 0
        
        # 建立三個 Embed 分頁
        embed_general = discord.Embed(title="一般指令", color=0xff3846, description="任何人都可以使用的基本指令")
        embed_general.add_field(name="/about", value="ℹ️ 關於 TWERG BOT 的資訊", inline=False)
        embed_general.add_field(name="/dyfi", value="📃 最新一筆 TWERG 體感回報的網址和簡易地震報告", inline=False)
        embed_general.add_field(name="/eewnow", value="🌐 查詢 地牛Wake Up! 的在線人數", inline=False)
        embed_general.add_field(name="/earlyest", value="🌊 查詢近期 Early-est 地震與海嘯潛勢自動解算", inline=False)
        embed_general.add_field(name="/earthquake_report_update", value="📝 查詢 2026/10/06 起記錄的顯著有感地震報告新舊資料", inline=False)
        embed_general.add_field(name="/emulator", value="🌍 簡易地震模擬器", inline=False)
        embed_general.add_field(name="/help", value="🛠️ 使用幫助", inline=False)
        embed_general.add_field(name="/intensity", value="🌍 透過輸入 PGA 和 PGV 大約換算各國地震震度", inline=False)
        embed_general.add_field(name="/invite", value="🔗 取得地牛記錄小組的 Discord 邀請網址", inline=False)
        embed_general.add_field(name="/kkw", value="💥 計算地震規模相等於多少顆原子彈的能量", inline=False)
        embed_general.add_field(name="/rmt", value="📡 查詢近期 BATS RMT 地震報告", inline=False)
        embed_general.add_field(name="/yt", value="🖥️ 查詢 台灣地震監視 YouTube 直播觀看人數", inline=False)
        
        embed_admin = discord.Embed(title="管理員指令", color=0xff3846, description="需要管理員權限才能使用的指令")
        embed_admin.add_field(name="/settings", value="⚙️ 顯示或修改機器人的設定\n-# 含 🇰🇷 韓國地震 (KMA)：預警／報告推送頻道、警報 tag 身分組、斷線通知", inline=False)
        embed_admin.add_field(name="/kma_status", value="🇰🇷 查看韓國氣象廳 (KMA) 預警／報告／測站連線狀態", inline=False)
        embed_admin.add_field(name="/sync_earthquake_reports", value="🔄 依 `/settings` 資料來源設定手動同步地震報告", inline=False)
        
        embed_owner = discord.Embed(title="擁有者指令", color=0x9b59b6, description="僅限機器人擁有者使用的指令")
        embed_owner.add_field(name="/guilds", value="🤖 顯示機器人加入的伺服器列表與活躍狀態", inline=False)
        embed_owner.add_field(name="/leave", value="🚪 強制退出指定的伺服器", inline=False)
        embed_owner.add_field(name="/broadcast", value="📢 對所有開啟自動推送的伺服器發送廣播", inline=False)
        embed_owner.add_field(name="/kma_token", value="🔑 設定韓國氣象廳 (KMA) 資料連線 Token（不需重啟）", inline=False)
        embed_owner.add_field(name="/kma_backup", value="🛡️ 設定韓國地震備援伺服器網址，留空即停用（不需重啟）", inline=False)
        embed_owner.add_field(name="/shutdown", value="🛑 關閉 BOT", inline=False)
        embed_owner.add_field(name="/restart", value="🔄 重新啟動機器人", inline=False)

        embed_kma = discord.Embed(
            title="🇰🇷 韓國地震 (KMA) 使用指南",
            color=0x0F2C59,
            description="自動推送韓國氣象廳（기상청）的地震預警與地震報告，訊息以韓文為主、中文為輔。",
        )
        embed_kma.add_field(
            name="📌 快速開始",
            value=(
                "**1.** 擁有者在 `config.json` 填入 `KMA_WS_TOKEN`，或執行 `/kma_token`\n"
                "**2.** 管理員執行 `/settings` → 選擇「🇰🇷 韓國地震 (KMA) 推送」\n"
                "**3.** 開啟預警／報告推送，並選擇推送頻道\n"
                "**4.** 執行 `/kma_status`，確認三條連線皆為 🟢"
            ),
            inline=False,
        )
        embed_kma.add_field(
            name="📨 訊息種類",
            value=(
                "🚨 **지진조기경보**（地震預警）：規模 5.0 以上\n"
                "⚠️ **지진속보**（地震速報）：陸地規模 3.5〜4.9、海域 4.0〜4.9\n"
                "📋 **지진정보**（地震報告）：規模 2.0 以上，可設定最低推送規模\n"
                "-# 同一預警的後續報（제2보、제3보…）會直接編輯原訊息"
            ),
            inline=False,
        )
        embed_kma.add_field(
            name="🗺️ 地圖與震度",
            value=(
                "震度採韓國氣象廳 MMI 等級 Ⅰ〜Ⅹ 與官方配色\n"
                "預警地圖與城市 S 波抵達時間為 TWERG 推算，僅供參考\n"
                "報告地圖優先使用 KMA 即時測站實測值"
            ),
            inline=False,
        )
        embed_kma.add_field(
            name="🔔 警報 tag（選用）",
            value=(
                "`/settings` → 韓國地震 → 「警報 tag 設定」\n"
                "預設依韓國災難簡訊規則：지진조기경보一律 tag，지진속보最大預估震度 Ⅴ 以上才 tag\n"
                "-# 身分組需允許被提及，或機器人需有「提及所有身分組」權限"
            ),
            inline=False,
        )
        embed_kma.add_field(
            name="📡 斷線通知（建議開啟）",
            value=(
                "`/settings` → 韓國地震 → 「斷線通知設定」\n"
                "連線中斷超過 60 秒會通知並附上原因，可 tag 指定的人或身分組\n"
                "設定後可按「發送測試通知」確認頻道與 tag 是否正確"
            ),
            inline=False,
        )
        embed_kma.add_field(
            name="🛡️ 強化伺服器穩定性（選用）",
            value=(
                "擁有者以 `/kma_backup` 設定備援伺服器後，`/settings` → 韓國地震 會出現此按鈕\n"
                "開啟後同時接收主要與備援來源，任一來源先到就先推送，不會重複發送\n"
                "-# 未設定備援伺服器時不會顯示此按鈕"
            ),
            inline=False,
        )
        embed_kma.add_field(
            name="❓ 沒有收到推送？",
            value=(
                "• `/kma_status` 顯示 🔴 → 檢查 Token 或網路，原因會顯示在狀態中\n"
                "• 報告沒推送 → 確認規模是否低於設定的最低推送規模\n"
                "• 機器人沒有回應 → 確認機器人在該頻道有「傳送訊息」「嵌入連結」「附加檔案」權限"
            ),
            inline=False,
        )

        self.pages = [embed_general, embed_admin, embed_owner, embed_kma]
        self.update_buttons()

    def update_buttons(self):
        # 若在第一頁則禁用上一頁，在最後一頁則禁用下一頁
        self.children[0].disabled = self.current_page == 0
        self.children[2].disabled = self.current_page == len(self.pages) - 1
        # 更新頁碼指示器
        self.children[1].label = f"第 {self.current_page + 1} / {len(self.pages)} 頁"

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.primary, row=0)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="第 1 / 3 頁", style=discord.ButtonStyle.secondary, disabled=True, row=0)
    async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass # 這個按鈕只作為文字顯示用，永遠被禁用

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.primary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="顯示 TWERG BOT 的使用幫助與可用指令清單")
    async def help_command(self, interaction: discord.Interaction):
        message_content = "🛠️ TWERG BOT 使用幫助"
        view = HelpView()
        await interaction.response.send_message(content=message_content, embed=view.pages[0], view=view)

async def setup(bot):
    bot.remove_command("help") # 移除 discord.py 預設的 help 指令
    await bot.add_cog(HelpCog(bot))
