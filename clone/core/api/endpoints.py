"""
MiraiMind & Momo Platform API Endpoints (core.api.endpoints).

Extracted directly from native binaries (MiraimindTool / TTMTool v2.6.3).
"""

API_BASE = "https://api.miraimind.ai/miraimind-server/api/app"
MELON_BASE = "https://melon-gateway-os.immomo.com/miraimind-server/api/app"
LOG_BASE = "https://app-log-oversea.immomo.com"
DATAWAREHOUSE_BASE = "https://melon-gateway-os.immomo.com/datawarehouse/miraimind/point/report"

# ─── USER & AUTH ─────────────────────────────────────────────────────────────
LOGIN_GUEST_URL = f"{API_BASE}/user/loginGuest"
LOGIN_WEB_URL = f"{API_BASE}/user/loginWeb"
SEND_EMAIL_VERIFY_CODE_URL = f"{MELON_BASE}/user/sendEmailVerifyCode"
USER_INFO_URL = f"{API_BASE}/user/getUserInfo"
BIND_PUSH_TOKEN_URL = f"{API_BASE}/user/bindPushToken"
UP_SETTING_URL = f"{MELON_BASE}/user/upSetting"
UPDATE_GENDER_PREFERENCE_URL = f"{MELON_BASE}/user/updateGenderPreference"
AGE_RANGE_TIPS_URL = f"{API_BASE}/user/ageRangeTips"

# ─── MALL, POINTS & INVITATION ───────────────────────────────────────────────
INVITATION_SUBMIT_URL = f"{API_BASE}/mall/invitation/submit"
USER_BALANCE_URL = f"{API_BASE}/mall/getUserBalance"
CHECK_PENDING_TASK_URL = f"{API_BASE}/mall/checkPendingTask"
MALL_TASKS_URL = f"{API_BASE}/mall/tasks"
SPECIAL_TASKS_URL = f"{API_BASE}/mall/specialTasks"
RECHARGE_PROGRESS_URL = f"{API_BASE}/mall/recharge/progress"
DAILY_BONUS_ENTRANCE_URL = f"{API_BASE}/mall/ad/dailyBonusEntrance"
AWARD_EXCHANGE_URL = f"{MELON_BASE}/mall/award/exchange"
RECEIVE_CHAT_COIN_PRIVILEGE_URL = f"{MELON_BASE}/mall/receiveChatCoinPrivilege"
GET_GUEST_CHAT_TURNS_URL = f"{MELON_BASE}/mall/getGuestChatTurns"
GET_NEW_CHAT_FREE_COUNT_URL = f"{MELON_BASE}/mall/getNewChatFreeCount"
GUEST_REGISTER_AWARD_URL = f"{API_BASE}/common/getGuestRegisterAward"

# ─── BOTS, ALBUMS & CREATORS ──────────────────────────────────────────────────
BOT_PROFILE_URL = f"{API_BASE}/bot/profile"
USER_BOT_LIST_URL = f"{API_BASE}/user/userBotList"
GET_BOT_ALBUMS_URL = f"{API_BASE}/album/getBotAlbums"
UNLOCK_BOT_ALBUM_URL = f"{API_BASE}/creator/unlockBotAlbum"
CHECK_PERMISSION_BACKGROUND_URL = f"{API_BASE}/album/checkPermissionForSettingBackground"
CREATE_BOT_INFO_URL = f"{API_BASE}/createBot/information"
FOLLOW_ONE_URL = f"{API_BASE}/follow/followOne"
FEED_LIKE_URL = f"{API_BASE}/feed/like"
PUBLISHED_FEED_LIST_URL = f"{API_BASE}/feed/publishedFeedList"

# ─── CHAT & SOCIAL ───────────────────────────────────────────────────────────
STREAM_PULL_URL = f"{API_BASE}/chat/stream/pull"
QUICK_REPLY_URL = f"{API_BASE}/chat/quickReply"
QUICK_REPLY_LIST_URL = f"{API_BASE}/chat/quickReplyList"
CHAT_PAGE_EXPOSED_URL = f"{API_BASE}/chat/chatPageExposed"
CHAT_TIPS_URL = f"{API_BASE}/chat/chatTips"
CHAT_EXPOSURE_URL = f"{API_BASE}/chat/exposure"
USER_CHAT_PRIVILEGE_URL = f"{API_BASE}/chat/getUserChatPrivilege"
CHAT_BOTTOM_OPTIONS_URL = f"{API_BASE}/chatSettings/getBottomOptions"
CHAT_TOOLBARS_URL = f"{API_BASE}/chatSettings/getToolbars"
SESSION_LIST_URL = f"{API_BASE}/session/sessionList"
SESSION_GET_BOT_INFO_URL = f"{API_BASE}/session/getBotInfo"
SESSION_BOT_REALTIME_STATUS_URL = f"{API_BASE}/session/getBotRealTimeStatus"
TRACK_RESUME_FOREGROUND_URL = f"{API_BASE}/chat/TrackResumeForeground"
TRACK_IM_CHAT_PAGE_EXPOSURE_URL = f"{API_BASE}/chat/trackImChatPageExposure"

# ─── COMMON & CONFIG ──────────────────────────────────────────────────────────
APP_CONFIG_URL = f"{API_BASE}/common/appConfig"
APP_REPORT_URL = f"{API_BASE}/common/appReport"
GET_CONFIG_URL = f"{API_BASE}/common/getConfig"
GENDER_PREFERENCE_CONFIG_URL = f"{API_BASE}/common/getGenderPreferenceConfig"
OFFICIAL_LINKS_URL = f"{API_BASE}/common/officialLinks"
PAGE_EXPOSURE_URL = f"{API_BASE}/common/page/exposure"
SETTING_CONFIG_URL = f"{API_BASE}/common/settingConfig"
START_APP_AD_CONFIG_URL = f"{API_BASE}/startAppAd/getStartAppAdConfig"
FIRST_MONTH_VIP_DISCOUNT_URL = f"{API_BASE}/firstMonthVipDiscount/info"
DISLIKE_SEARCH_OPTIONS_URL = f"{API_BASE}/dislike/search/options"
INTRANET_CONFIG_URL = "https://melon-raburo.immomo.com/miraimind-server/api/app/intranet/config"

# ─── HARDWARE & MMUID ATTESTATION ────────────────────────────────────────────
MMUID_SUPPLEMENT_V1_URL = f"{LOG_BASE}/v1/log/upload_info/supplement"
MMUID_SUPPLEMENT_V3_URL = f"{LOG_BASE}/v3/log/upload_info/supplement"
HDATA_UPLOAD_URL = f"{LOG_BASE}/v1/log/upload_info"
TELEMETRY_POINT_REPORT_URL = f"{DATAWAREHOUSE_BASE}/saveSchemaBatch/noencrypt/datainf_hellodata_client_point"
TELEMETRY_ELEMENTS_URL = f"{DATAWAREHOUSE_BASE}/noencrypt/getElements"

# ─── ACCOUNTING & REWARDS ─────────────────────────────────────────────────────
CUSTOMER_INVITATION_REWARD_POINTS = 200
DEFAULT_MDP_LOGIN_TYPE = 13
