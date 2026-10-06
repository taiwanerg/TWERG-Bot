import discord
from discord.ext import commands
from discord import app_commands
import json
import os

# 定義儲存各伺服器設定的檔案路徑
SETTINGS_FILE = 'guild_settings.json'

def load_settings():
    """讀取伺服器設定檔"""
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_settings(data):
    """寫入伺服器設定檔"""
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

class SettingsOverviewView(discord.ui.View):
    def __init__(self, guild_id: int | str):
        super().__init__(timeout=None)
        self.guild_id = str(guild_id)
        self.all_settings = load_settings()
        self.settings = self.all_settings.get(self.guild_id, {})

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="`🛠️` 伺服器設定概覽",
            description="請從下方選單選擇要調整的功能。",
            color=0x2b2d31
        )
        
        eq_status = "`🟢` 已啟用" if self.settings.get("auto_push") else "`🔴` 已停用"
        yt_status = "`🟢` 已啟用" if self.settings.get("yt_monitor_enabled") else "`🔴` 已停用"
        rmt_status = "`🟢` 已啟用" if self.settings.get("rmt_monitor_enabled") else "`🔴` 已停用"
        grmt_status = "`🟢` 已啟用" if self.settings.get("grmt_monitor_enabled") else "`🔴` 已停用"
        earlyest_status = "`🟢` 已啟用" if self.settings.get("earlyest_monitor_enabled") else "`🔴` 已停用"
        auto_pub_status = "`🟢` 已啟用" if self.settings.get("auto_publish_news") else "`🔴` 已停用"
        revision_status = "`🟢` 已啟用" if self.settings.get("report_revision_enabled") else "`🔴` 已停用"
        if self.settings.get("report_revision_exptech_enabled", False):
            revision_status += "（ExpTech v2 補強）"
        
        embed.add_field(name="⚙️ TWERG 體感回報設定", value=eq_status, inline=False)
        embed.add_field(name="🖥️ YouTube 直播監控", value=yt_status, inline=False)
        embed.add_field(name="📡 RMT 推送設定", value=rmt_status, inline=False)
        embed.add_field(name="🌍 GRMT 推送設定", value=grmt_status, inline=False)
        embed.add_field(name="🌊 Early-est 推送設定", value=earlyest_status, inline=False)
        embed.add_field(name="📢 公告頻道自動發布", value=auto_pub_status, inline=False)
        embed.add_field(name="📝 地震報告更新推送", value=revision_status, inline=False)
        
        return embed

    @discord.ui.select(
        placeholder="選擇要設定的項目...",
        options=[
            discord.SelectOption(label="TWERG 體感回報設定", value="eq", emoji="⚙️", description="自動推送最新地震報告與體感統計"),
            discord.SelectOption(label="YouTube 直播監控設定", value="yt", emoji="🖥️", description="監控地震直播人數異常增加"),
            discord.SelectOption(label="RMT 推送設定", value="rmt", emoji="📡", description="自動推送 RMT 即時地震動報告"),
            discord.SelectOption(label="GRMT 推送設定", value="grmt", emoji="🌍", description="自動推送 Global RMT 地震動報告"),
            discord.SelectOption(label="Early-est 推送設定", value="earlyest", emoji="🌊", description="自動推送 Early-est 即時地震報告"),
            discord.SelectOption(label="公告自動發布設定", value="auto_pub", emoji="📢", description="自動發布公告頻道的訊息"),
            discord.SelectOption(label="地震報告更新推送", value="revision", emoji="📝", description="推送中央氣象署重新測定的地震資料")
        ],
        row=0
    )
    async def select_category(self, interaction: discord.Interaction, select: discord.ui.Select):
        val = select.values[0]
        if val == "eq":
            view = SettingsView(self.guild_id)
        elif val == "yt":
            view = YTSettingsView(self.guild_id)
        elif val == "rmt":
            view = RMTSettingsView(self.guild_id)
        elif val == "grmt":
            view = GRMTSettingsView(self.guild_id)
        elif val == "earlyest":
            view = EarlyEstSettingsView(self.guild_id)
        elif val == "auto_pub":
            view = AutoPublishSettingsView(self.guild_id)
        elif val == "revision":
            view = EarthquakeRevisionSettingsView(self.guild_id)
        
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    @discord.ui.button(label="關閉設定", style=discord.ButtonStyle.danger, row=1)
    async def close_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ **設定已關閉**", embed=None, view=None)
        self.stop()

class YTSettingsView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = str(guild_id)
        self.all_settings = load_settings()
        
        if self.guild_id not in self.all_settings:
            self.all_settings[self.guild_id] = {}
            
        self.settings = self.all_settings[self.guild_id]
        
        # 若無監控設定則初始化預設值
        if "yt_monitor_enabled" not in self.settings:
            self.settings["yt_monitor_enabled"] = False
        if "yt_target_channel_ids" not in self.settings:
            self.settings["yt_target_channel_ids"] = []
        if "yt_monitor_threshold" not in self.settings:
            self.settings["yt_monitor_threshold"] = 1000

    def build_embed(self) -> discord.Embed:
        """建立 YouTube 監控設定 Embed 排版"""
        embed = discord.Embed(
            title="`🖥️` YouTube 直播監控設定",
            description="調整當前伺服器的 YouTube 觀看人數監控選項。",
            color=0xffffff
        )
        
        status = "`🟢` 已啟用" if self.settings.get("yt_monitor_enabled") else "`🔴` 已停用"
        channel_ids = self.settings.get("yt_target_channel_ids", [])
        channel_status = "\n".join([f"<#{c_id}>" for c_id in channel_ids]) if channel_ids else "⚠️ 尚未設定"
        threshold = self.settings.get("yt_monitor_threshold", 1000)
        
        embed.add_field(name="監控狀態", value=status, inline=False)
        embed.add_field(name="監控發送頻道列表", value=channel_status, inline=False)
        embed.add_field(name="監控變動人數閾值", value=f"增加 {threshold} 人以上", inline=False)
        
        return embed

    @discord.ui.button(label="切換監控狀態", style=discord.ButtonStyle.primary, row=0)
    async def toggle_yt_monitor(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["yt_monitor_enabled"] = not self.settings.get("yt_monitor_enabled", False)
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        
    @discord.ui.select(
        cls=discord.ui.ChannelSelect, 
        channel_types=[discord.ChannelType.text], 
        placeholder="選擇監控發送頻道 (可多選，將覆蓋原設定)", 
        min_values=0,
        max_values=25,
        row=1
    )
    async def select_yt_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.settings["yt_target_channel_ids"] = [c.id for c in select.values]
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.select(
        placeholder="選擇變動人數閾值",
        options=[
            discord.SelectOption(label="增加 500 人以上", value="500"),
            discord.SelectOption(label="增加 1000 人以上（預設）", value="1000"),
            discord.SelectOption(label="增加 2000 人以上", value="2000"),
            discord.SelectOption(label="增加 5000 人以上", value="5000"),
            discord.SelectOption(label="增加 10000 人以上", value="10000"),
            discord.SelectOption(label="增加 20000 人以上", value="20000"),
            discord.SelectOption(label="增加 50000 人以上", value="50000"),
        ],
        row=2
    )
    async def select_yt_threshold(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.settings["yt_monitor_threshold"] = int(select.values[0])
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="返回概覽", style=discord.ButtonStyle.secondary, row=3)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SettingsOverviewView(self.guild_id)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    @discord.ui.button(label="完成設定", style=discord.ButtonStyle.success, row=3)
    async def finish_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="✅ **設定已儲存**", 
            embed=self.build_embed(), 
            view=None
        )
        self.stop()

class RMTSettingsView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = str(guild_id)
        self.all_settings = load_settings()
        
        if self.guild_id not in self.all_settings:
            self.all_settings[self.guild_id] = {}
            
        self.settings = self.all_settings[self.guild_id]
        
        # 若無監控設定則初始化預設值
        if "rmt_monitor_enabled" not in self.settings:
            self.settings["rmt_monitor_enabled"] = False
        if "rmt_target_channel_ids" not in self.settings:
            self.settings["rmt_target_channel_ids"] = []

    def build_embed(self) -> discord.Embed:
        """建立 RMT 自動推送設定 Embed 排版"""
        embed = discord.Embed(
            title="`📡` RMT 地震報告自動推送設定",
            description="調整當前伺服器的 RMT 報告自動推送選項。",
            color=0x3498db
        )
        
        status = "`🟢` 已啟用" if self.settings.get("rmt_monitor_enabled") else "`🔴` 已停用"
        channel_ids = self.settings.get("rmt_target_channel_ids", [])
        channel_status = "\n".join([f"<#{c_id}>" for c_id in channel_ids]) if channel_ids else "⚠️ 尚未設定"
        
        embed.add_field(name="推送狀態", value=status, inline=False)
        embed.add_field(name="推送發送頻道列表", value=channel_status, inline=False)
        
        return embed

    @discord.ui.button(label="切換推送狀態", style=discord.ButtonStyle.primary, row=0)
    async def toggle_rmt_monitor(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["rmt_monitor_enabled"] = not self.settings.get("rmt_monitor_enabled", False)
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        
    @discord.ui.select(
        cls=discord.ui.ChannelSelect, 
        channel_types=[discord.ChannelType.text], 
        placeholder="選擇推送發送頻道 (可多選，將覆蓋原設定)", 
        min_values=0,
        max_values=25,
        row=1
    )
    async def select_rmt_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.settings["rmt_target_channel_ids"] = [c.id for c in select.values]
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="返回概覽", style=discord.ButtonStyle.secondary, row=2)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SettingsOverviewView(self.guild_id)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    @discord.ui.button(label="完成設定", style=discord.ButtonStyle.success, row=2)
    async def finish_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="✅ **設定已儲存**", 
            embed=self.build_embed(), 
            view=None
        )
        self.stop()

class GRMTSettingsView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = str(guild_id)
        self.all_settings = load_settings()
        
        if self.guild_id not in self.all_settings:
            self.all_settings[self.guild_id] = {}
            
        self.settings = self.all_settings[self.guild_id]
        
        if "grmt_monitor_enabled" not in self.settings:
            self.settings["grmt_monitor_enabled"] = False
        if "grmt_target_channel_ids" not in self.settings:
            self.settings["grmt_target_channel_ids"] = []
        if "grmt_only_significant" not in self.settings:
            self.settings["grmt_only_significant"] = False

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="`🌍` Global RMT 地震報告自動推送設定",
            description="調整當前伺服器的 GRMT 報告自動推送選項。",
            color=0x3498db
        )
        
        status = "`🟢` 已啟用" if self.settings.get("grmt_monitor_enabled") else "`🔴` 已停用"
        sig_status = "`🟢` 已啟用" if self.settings.get("grmt_only_significant") else "`🔴` 已停用"
        channel_ids = self.settings.get("grmt_target_channel_ids", [])
        channel_status = "\n".join([f"<#{c_id}>" for c_id in channel_ids]) if channel_ids else "⚠️ 尚未設定"
        
        embed.add_field(name="推送狀態", value=status, inline=False)
        embed.add_field(name="僅推播重大地震", value=sig_status, inline=False)
        embed.add_field(name="推送發送頻道列表", value=channel_status, inline=False)
        
        return embed

    @discord.ui.button(label="切換推送狀態", style=discord.ButtonStyle.primary, row=0)
    async def toggle_grmt_monitor(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["grmt_monitor_enabled"] = not self.settings.get("grmt_monitor_enabled", False)
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        
    @discord.ui.button(label="切換僅推播重大地震", style=discord.ButtonStyle.primary, row=0)
    async def toggle_grmt_significant(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["grmt_only_significant"] = not self.settings.get("grmt_only_significant", False)
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        
    @discord.ui.select(
        cls=discord.ui.ChannelSelect, 
        channel_types=[discord.ChannelType.text], 
        placeholder="選擇推送發送頻道 (可多選，將覆蓋原設定)", 
        min_values=0,
        max_values=25,
        row=1
    )
    async def select_grmt_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.settings["grmt_target_channel_ids"] = [c.id for c in select.values]
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="返回概覽", style=discord.ButtonStyle.secondary, row=2)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SettingsOverviewView(self.guild_id)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    @discord.ui.button(label="完成設定", style=discord.ButtonStyle.success, row=2)
    async def finish_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="✅ **設定已儲存**", 
            embed=self.build_embed(), 
            view=None
        )
        self.stop()

class EarlyEstSettingsView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = str(guild_id)
        self.all_settings = load_settings()

        if self.guild_id not in self.all_settings:
            self.all_settings[self.guild_id] = {}

        self.settings = self.all_settings[self.guild_id]
        if "earlyest_monitor_enabled" not in self.settings:
            self.settings["earlyest_monitor_enabled"] = False
        if "earlyest_target_channel_ids" not in self.settings:
            self.settings["earlyest_target_channel_ids"] = []
        if "earlyest_mb_threshold" not in self.settings:
            self.settings["earlyest_mb_threshold"] = "all"

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="`🌊` Early-est 地震報告自動推送設定",
            description="調整當前伺服器的 Early-est 報告自動推送選項。",
            color=0xe67e22,
        )

        status = "`🟢` 已啟用" if self.settings.get("earlyest_monitor_enabled") else "`🔴` 已停用"
        channel_ids = self.settings.get("earlyest_target_channel_ids", [])
        channel_status = "\n".join([f"<#{channel_id}>" for channel_id in channel_ids]) if channel_ids else "⚠️ 尚未設定"
        threshold = self.settings.get("earlyest_mb_threshold", "all")
        threshold_status = "全部" if threshold == "all" else f"mb ≥ {threshold}"

        embed.add_field(name="推送狀態", value=status, inline=False)
        embed.add_field(name="推送發送頻道列表", value=channel_status, inline=False)
        embed.add_field(name="mb 規模門檻", value=threshold_status, inline=False)
        embed.set_footer(text="Early-est 為未經人工審核的實驗性自動解算，不應作為海嘯警報依據。")
        return embed

    @discord.ui.button(label="切換推送狀態", style=discord.ButtonStyle.primary, row=0)
    async def toggle_earlyest_monitor(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["earlyest_monitor_enabled"] = not self.settings.get("earlyest_monitor_enabled", False)
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="選擇推送發送頻道 (可多選，將覆蓋原設定)",
        min_values=0,
        max_values=25,
        row=1,
    )
    async def select_earlyest_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.settings["earlyest_target_channel_ids"] = [channel.id for channel in select.values]
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.select(
        placeholder="選擇 Early-est mb 推送門檻",
        options=[
            discord.SelectOption(label="全部", value="all"),
            discord.SelectOption(label="mb 4.5 以上", value="4.5"),
            discord.SelectOption(label="mb 5.0 以上", value="5.0"),
            discord.SelectOption(label="mb 5.5 以上", value="5.5"),
            discord.SelectOption(label="mb 6.0 以上", value="6.0"),
            discord.SelectOption(label="mb 6.5 以上", value="6.5"),
            discord.SelectOption(label="mb 7.0 以上", value="7.0"),
        ],
        row=2,
    )
    async def select_earlyest_mb_threshold(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.settings["earlyest_mb_threshold"] = select.values[0]
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="返回概覽", style=discord.ButtonStyle.secondary, row=3)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SettingsOverviewView(self.guild_id)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    @discord.ui.button(label="完成設定", style=discord.ButtonStyle.success, row=3)
    async def finish_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="✅ **設定已儲存**",
            embed=self.build_embed(),
            view=None,
        )
        self.stop()


class SettingsView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None) # 取消 Timeout，讓設定面板持續有效
        self.guild_id = str(guild_id)
        self.all_settings = load_settings()
        
        # 若該伺服器尚未有設定，初始化預設值
        if self.guild_id not in self.all_settings:
            self.all_settings[self.guild_id] = {
                "auto_push": False,
                "target_channel_ids": [],
                "min_magnitude": 4.0,
                "auto_dyfi_report": True
            }
        
        self.settings = self.all_settings[self.guild_id]
        
        # 兼容舊版設定檔
        if "target_channel_ids" not in self.settings:
            self.settings["target_channel_ids"] = []
            if self.settings.get("target_channel_id"):
                self.settings["target_channel_ids"].append(self.settings["target_channel_id"])
                
        # 兼容設定檔，確保新功能有預設值
        if "auto_dyfi_report" not in self.settings:
            self.settings["auto_dyfi_report"] = True
        if "render_map" not in self.settings:
            self.settings["render_map"] = True
        if "discord_dyfi" not in self.settings:
            self.settings["discord_dyfi"] = True

        # 初始化時，根據目前的設定狀態來決定下拉選單各選項的「預設打勾狀態」
        for child in self.children:
            if isinstance(child, discord.ui.Select) and child.placeholder == "點此開啟或關閉功能 (可多選)":
                for option in child.options:
                    if option.value == "auto_push":
                        option.default = self.settings.get("auto_push", False)
                    elif option.value == "auto_dyfi_report":
                        option.default = self.settings.get("auto_dyfi_report", True)
                    elif option.value == "render_map":
                        option.default = self.settings.get("render_map", True)
                    elif option.value == "discord_dyfi":
                        option.default = self.settings.get("discord_dyfi", True)

    def build_embed(self) -> discord.Embed:
        """根據當前設定建立 Embed 排版"""
        embed = discord.Embed(
            title="`⚙️` 伺服器 TWERG 體感回報推送設定",
            description="調整當前伺服器的自動推送選項。",
            color=0xff3846
        )
        
        # 解析狀態
        auto_push_status = "`🟢` 已啟用" if self.settings.get("auto_push") else "`🔴` 已停用"
        auto_dyfi_status = "`🟢` 已啟用" if self.settings.get("auto_dyfi_report", True) else "`🔴` 已停用"
        render_map_status = "`🟢` 已啟用" if self.settings.get("render_map", True) else "`🔴` 已停用"
        discord_dyfi_status = "`🟢` 已啟用" if self.settings.get("discord_dyfi", True) else "`🔴` 已停用"
        channel_ids = self.settings.get("target_channel_ids", [])
        channel_status = "\n".join([f"<#{c_id}>" for c_id in channel_ids]) if channel_ids else "⚠️ 尚未設定"
        min_mag = self.settings.get("min_magnitude", 4.0)
        
        embed.add_field(name="自動推送狀態", value=auto_push_status, inline=False)
        embed.add_field(name="30分鐘後初步統計", value=auto_dyfi_status, inline=False)
        embed.add_field(name="初步統計包含地圖圖片", value=f"{render_map_status}\n-# 停用不影響 /dyfi 指令", inline=False)
        embed.add_field(name="整合 Discord 體感回報", value=f"{discord_dyfi_status}\n-# 將 Discord 頻道的訊息回報納入統計與地圖中", inline=False)
        embed.add_field(name="推送目標頻道列表", value=channel_status, inline=False)
        embed.add_field(name="最低推送規模", value=f"芮氏 {min_mag}", inline=False)
        
        return embed

    @discord.ui.select(
        placeholder="點此開啟或關閉功能 (可多選)",
        min_values=0,
        max_values=4,
        options=[
            discord.SelectOption(label="自動推送", value="auto_push", description="啟用自動推送地震報告", emoji="📨"),
            discord.SelectOption(label="初步統計", value="auto_dyfi_report", description="發送 30 分鐘後初步統計", emoji="🕟"),
            discord.SelectOption(label="地圖渲染", value="render_map", description="初步統計包含地圖圖片", emoji="🗺️"),
            discord.SelectOption(label="Discord 體感", value="discord_dyfi", description="整合 Discord 體感回報", emoji="💬")
        ],
        row=0
    )
    async def toggle_switches(self, interaction: discord.Interaction, select: discord.ui.Select):
        """利用多選選單切換各種功能"""
        self.settings["auto_push"] = "auto_push" in select.values
        self.settings["auto_dyfi_report"] = "auto_dyfi_report" in select.values
        self.settings["render_map"] = "render_map" in select.values
        self.settings["discord_dyfi"] = "discord_dyfi" in select.values
        
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)

        # 更新選單的預設勾選狀態以反映操作結果
        for option in select.options:
            option.default = option.value in select.values

        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect, 
        channel_types=[discord.ChannelType.text], 
        placeholder="選擇推送目標頻道 (可多選，將覆蓋原設定)", 
        min_values=0,
        max_values=25,
        row=1
    )
    async def select_target_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        """選擇推送頻道"""
        self.settings["target_channel_ids"] = [c.id for c in select.values]
        
        # 儲存並更新介面
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.select(
        placeholder="選擇最低推送規模",
        options=[
            discord.SelectOption(label="規模 4.0 以上", value="4.0"),
            discord.SelectOption(label="規模 4.5 以上", value="4.5"),
            discord.SelectOption(label="規模 5.0 以上", value="5.0"),
            discord.SelectOption(label="規模 5.5 以上", value="5.5"),
            discord.SelectOption(label="規模 6.0 以上", value="6.0"),
        ],
        row=2
    )
    async def select_min_magnitude(self, interaction: discord.Interaction, select: discord.ui.Select):
        """選擇觸發推送的最低規模"""
        self.settings["min_magnitude"] = float(select.values[0])
        
        # 儲存並更新介面
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="返回概覽", style=discord.ButtonStyle.secondary, row=3)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SettingsOverviewView(self.guild_id)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    @discord.ui.button(label="完成設定", style=discord.ButtonStyle.success, row=3)
    async def finish_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        """其實沒有特別作用的確認按鈕"""
        await interaction.response.edit_message(
            content="✅ **設定已儲存**", 
            embed=self.build_embed(), 
            view=None
        )
        self.stop()

class EarthquakeRevisionSettingsView(discord.ui.View):
    """管理重新定位／測算通知，不與即時地震推播共用開關。"""
    def __init__(self, guild_id: int | str):
        super().__init__(timeout=None)
        self.guild_id = str(guild_id)
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {})
        self.settings.setdefault("report_revision_enabled", False)
        self.settings.setdefault("report_revision_channel_ids", [])
        self.settings.setdefault("report_revision_exptech_enabled", False)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="`📝` 地震報告更新推送設定",
            description=(
                "每天上午 8 點與下午 8 點同步地震報告。CWA API 固定啟用；"
                "可額外啟用 ExpTech v2 比對開放資料未及時反映的數值。"
            ),
            color=0x3A3A44,
        )
        status = "`🟢` 已啟用" if self.settings["report_revision_enabled"] else "`🔴` 已停用"
        exptech_status = "`🟢` 已啟用" if self.settings["report_revision_exptech_enabled"] else "`🔴` 已停用"
        channel_ids = self.settings["report_revision_channel_ids"]
        channels = "\n".join(f"<#{channel_id}>" for channel_id in channel_ids) if channel_ids else "⚠️ 尚未設定"
        embed.add_field(name="推送狀態", value=status, inline=False)
        embed.add_field(name="CWA API", value="`🟢` 固定啟用", inline=True)
        embed.add_field(name="ExpTech v2 補強", value=exptech_status, inline=True)
        embed.add_field(name="推送目標頻道列表", value=channels, inline=False)
        return embed

    @discord.ui.button(label="切換推送狀態", style=discord.ButtonStyle.primary, row=0)
    async def toggle_enabled(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["report_revision_enabled"] = not self.settings["report_revision_enabled"]
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="切換 ExpTech v2 補強", style=discord.ButtonStyle.primary, row=1)
    async def toggle_exptech(self, interaction: discord.Interaction, button: discord.ui.Button):
        enabled = not self.settings.get("report_revision_exptech_enabled", False)
        self.settings["report_revision_exptech_enabled"] = enabled
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)

        if not enabled:
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
            return

        # Component defer keeps the ephemeral settings message editable while
        # the two upstream APIs are being reconciled.
        await interaction.response.defer()
        summary = None
        cog = interaction.client.get_cog("EarthquakeRevisionAlertCog")
        if cog is None:
            summary = "⚠️ ExpTech v2 已啟用，但地震報告同步模組尚未就緒，將於下次排程再嘗試。"
        else:
            try:
                result = await cog.enable_exptech_for_guild(self.guild_id)
                summary = (
                    f"✅ ExpTech v2 已啟用：找到 {result['differences']} 筆來源資料差異、"
                    f"{result['revisions']} 筆已確認修訂，送出 {result['sent']} 則頻道通知。"
                )
                if result["errors"]:
                    summary += "\n⚠️ " + "；".join(result["errors"])
            except Exception as error:
                summary = f"⚠️ ExpTech v2 已啟用，但首次同步失敗：{error}"

        await interaction.edit_original_response(embed=self.build_embed(), view=self)
        await interaction.followup.send(summary, ephemeral=True)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="選擇更新推送頻道 (可多選，將覆蓋原設定)",
        min_values=0,
        max_values=25,
        row=2,
    )
    async def select_channels(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.settings["report_revision_channel_ids"] = [channel.id for channel in select.values]
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="返回概覽", style=discord.ButtonStyle.secondary, row=3)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SettingsOverviewView(self.guild_id)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    @discord.ui.button(label="完成設定", style=discord.ButtonStyle.success, row=3)
    async def finish_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ **設定已儲存**", embed=self.build_embed(), view=None)
        self.stop()


class AutoPublishSettingsView(discord.ui.View):
    def __init__(self, guild_id: int | str):
        super().__init__(timeout=None)
        self.guild_id = str(guild_id)
        self.all_settings = load_settings()
        
        if self.guild_id not in self.all_settings:
            self.all_settings[self.guild_id] = {}
            
        self.settings = self.all_settings[self.guild_id]
        
        if "auto_publish_news" not in self.settings:
            self.settings["auto_publish_news"] = False

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="`📢` 公告頻道自動發布設定",
            description="開啟後，機器人將自動發布伺服器內所有「公告頻道 (News)」的新訊息。\n具有 Advanced URL Detection，可確保帶有網址的訊息也能正確發布 Embed。",
            color=0x9b59b6
        )
        
        status = "`🟢` 已啟用" if self.settings.get("auto_publish_news") else "`🔴` 已停用"
        embed.add_field(name="自動發布狀態", value=status, inline=False)
        
        return embed

    @discord.ui.button(label="切換狀態", style=discord.ButtonStyle.primary, row=0)
    async def toggle_auto_pub(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["auto_publish_news"] = not self.settings.get("auto_publish_news", False)
        self.all_settings[self.guild_id] = self.settings
        save_settings(self.all_settings)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="返回概覽", style=discord.ButtonStyle.secondary, row=1)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SettingsOverviewView(self.guild_id)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    @discord.ui.button(label="完成設定", style=discord.ButtonStyle.success, row=1)
    async def finish_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="✅ **設定已儲存**", 
            embed=self.build_embed(), 
            view=None
        )
        self.stop()

class SettingsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="settings", description="（限管理員）調整伺服器的自動推送與監控設定")
    @app_commands.guild_only()
    async def settings_command(self, interaction: discord.Interaction):
        # 改在執行階段檢查權限，確保 Discord 能正常註冊並顯示此指令。
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 此指令僅限伺服器管理員使用。", ephemeral=True)
            return
            
        # 初始化 View 與 Embed
        view = SettingsOverviewView(interaction.guild.id)
        embed = view.build_embed()
        
        # 傳送設定面板 (設為 ephemeral=True 代表僅有呼叫的管理員能看見與操作)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def setup(bot):
    await bot.add_cog(SettingsCog(bot))
