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
        embed_general.add_field(name="/emulator", value="🌍 簡易地震模擬器", inline=False)
        embed_general.add_field(name="/help", value="🛠️ 使用幫助", inline=False)
        embed_general.add_field(name="/intensity", value="🌍 透過輸入 PGA 和 PGV 大約換算各國地震震度", inline=False)
        embed_general.add_field(name="/invite", value="🔗 取得地牛記錄小組的 Discord 邀請網址", inline=False)
        embed_general.add_field(name="/kkw", value="💥 計算地震規模相等於多少顆原子彈的能量", inline=False)
        embed_general.add_field(name="/rmt", value="📡 查詢近期 BATS RMT 地震報告", inline=False)
        embed_general.add_field(name="/yt", value="🖥️ 查詢 台灣地震監視 YouTube 直播觀看人數", inline=False)
        
        embed_admin = discord.Embed(title="管理員指令", color=0xff3846, description="需要管理員權限才能使用的指令")
        embed_admin.add_field(name="/settings", value="⚙️ 顯示或修改機器人的設定", inline=False)
        embed_admin.add_field(name="/sync_earthquake_reports", value="🔄 依 `/settings` 資料來源設定手動同步地震報告", inline=False)
        
        embed_owner = discord.Embed(title="擁有者指令", color=0x9b59b6, description="僅限機器人擁有者使用的指令")
        embed_owner.add_field(name="/guilds", value="🤖 顯示機器人加入的伺服器列表與活躍狀態", inline=False)
        embed_owner.add_field(name="/leave", value="🚪 強制退出指定的伺服器", inline=False)
        embed_owner.add_field(name="/broadcast", value="📢 對所有開啟自動推送的伺服器發送廣播", inline=False)
        embed_owner.add_field(name="/shutdown", value="🛑 關閉 BOT", inline=False)
        embed_owner.add_field(name="/restart", value="🔄 重新啟動機器人", inline=False)

        self.pages = [embed_general, embed_admin, embed_owner]
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
