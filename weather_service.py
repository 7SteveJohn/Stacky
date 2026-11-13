"""
weather_service.py - 实时气象服务引擎
基于 Open-Meteo 公开免 Key 规范接口与 IP 自动定位，提供精准的天气数据获取与本地离线缓存。
完全非阻塞后台线程池调度，零网络卡顿。
"""

import json
import os
import ssl
import threading
import time
import urllib.request
import urllib.parse
from typing import Dict, Any, Optional
from PySide6.QtCore import QObject, Signal

# 常用城市经纬度与省份字典
POPULAR_CITIES: Dict[str, Dict[str, Any]] = {
    "成都": {"region": "四川", "lat": 30.6586, "lon": 104.0648},
    "北京": {"region": "北京", "lat": 39.9042, "lon": 116.4074},
    "上海": {"region": "上海", "lat": 31.2304, "lon": 121.4737},
    "广州": {"region": "广东", "lat": 23.1291, "lon": 113.2644},
    "深圳": {"region": "广东", "lat": 22.5431, "lon": 114.0579},
    "重庆": {"region": "重庆", "lat": 29.5630, "lon": 106.5516},
    "杭州": {"region": "浙江", "lat": 30.2741, "lon": 120.1551},
    "南京": {"region": "江苏", "lat": 32.0603, "lon": 118.7969},
    "武汉": {"region": "湖北", "lat": 30.5928, "lon": 114.3055},
    "西安": {"region": "陕西", "lat": 34.3416, "lon": 108.9398},
    "苏州": {"region": "江苏", "lat": 31.2990, "lon": 120.5853},
    "天津": {"region": "天津", "lat": 39.0842, "lon": 117.2009},
    "长沙": {"region": "湖南", "lat": 28.2282, "lon": 112.9388},
    "郑州": {"region": "河南", "lat": 34.7466, "lon": 113.6253},
    "合肥": {"region": "安徽", "lat": 31.8206, "lon": 117.2272},
    "福州": {"region": "福建", "lat": 26.0745, "lon": 119.2965},
    "厦门": {"region": "福建", "lat": 24.4798, "lon": 118.0894},
    "青岛": {"region": "山东", "lat": 36.0671, "lon": 120.3826},
    "济南": {"region": "山东", "lat": 36.6512, "lon": 117.1201},
    "昆明": {"region": "云南", "lat": 24.8797, "lon": 102.8332},
    "贵阳": {"region": "贵州", "lat": 26.6470, "lon": 106.6302},
    "南宁": {"region": "广西", "lat": 22.8170, "lon": 108.3665},
    "海口": {"region": "海南", "lat": 20.0440, "lon": 110.1999},
    "三亚": {"region": "海南", "lat": 18.2528, "lon": 109.5119},
    "南昌": {"region": "江西", "lat": 28.6820, "lon": 115.8579},
    "太原": {"region": "山西", "lat": 37.8706, "lon": 112.5489},
    "石家庄": {"region": "河北", "lat": 38.0428, "lon": 114.5149},
    "哈尔滨": {"region": "黑龙江", "lat": 45.8038, "lon": 126.5350},
    "长春": {"region": "吉林", "lat": 43.8171, "lon": 125.3235},
    "沈阳": {"region": "辽宁", "lat": 41.8057, "lon": 123.4315},
    "大连": {"region": "辽宁", "lat": 38.9140, "lon": 121.6147},
    "呼和浩特": {"region": "内蒙古", "lat": 40.8424, "lon": 111.7500},
    "兰州": {"region": "甘肃", "lat": 36.0611, "lon": 103.8343},
    "西宁": {"region": "青海", "lat": 36.6232, "lon": 101.7789},
    "银川": {"region": "宁夏", "lat": 38.4872, "lon": 106.2309},
    "乌鲁木齐": {"region": "新疆", "lat": 43.8256, "lon": 87.6168},
    "拉萨": {"region": "西藏", "lat": 29.6525, "lon": 91.1721},
    "香港": {"region": "香港", "lat": 22.3193, "lon": 114.1694},
    "澳门": {"region": "澳门", "lat": 22.1987, "lon": 113.5439},
    "台北": {"region": "台湾", "lat": 25.0330, "lon": 121.5654},
}

GEO_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".weather_geo.json")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import radians, sin, cos, asin, sqrt
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(a))


AREAS_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".weather_areas.json")


def _load_areas_cache() -> Dict[str, Any]:
    try:
        with open(AREAS_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_areas_cache(cache: Dict[str, Any]):
    try:
        with open(AREAS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass


def _datav_areas(adcode: int) -> list:
    """阿里 DataV 公开行政区划：返回该 adcode 下一级要素（name/adcode/center）。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = f"https://geo.datav.aliyun.com/areas_v3/bound/{adcode}_full.json"
    req = urllib.request.Request(url, headers={"User-Agent": "BigFolderLauncher-Weather/2.4"})
    with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out = []
    for feat in data.get("features", []):
        prop = feat.get("properties", {}) or {}
        center = prop.get("center") or prop.get("centroid")
        if not center:
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") or []
            if geom.get("type") == "MultiPolygon" and coords:
                ring = coords[0][0]
                center = [sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)]
        if prop.get("name") and center:
            out.append({
                "name": prop["name"],
                "adcode": prop.get("adcode"),
                "lat": round(float(center[1]), 5),
                "lon": round(float(center[0]), 5),
            })
    return out


CITY_INDEX_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".weather_cities.json")
_city_index_building = False
_city_index_lock = threading.Lock()


def _load_city_index() -> list:
    try:
        with open(CITY_INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("cities", [])
    except Exception:
        return []


def _normalize_city(name: str) -> str:
    for suffix in ("自治州", "地区", "特别行政区", "市辖区", "盟", "市", "县", "区"):
        name = name.replace(suffix, "")
    return name.strip()


def build_city_index(force: bool = False) -> list:
    """抓一次全国「省→市」索引并落盘缓存，供任意城市名解析使用（约 34 个请求，只跑一次）。"""
    global _city_index_building
    with _city_index_lock:
        if _city_index_building:
            return _load_city_index()
        cached = _load_city_index()
        if cached and not force:
            return cached
        _city_index_building = True
    try:
        cities = []
        for prov in _datav_areas(100000):
            try:
                for city in _datav_areas(int(prov["adcode"])):
                    cities.append({
                        "name": city["name"],
                        "adcode": city.get("adcode"),
                        "lat": city["lat"],
                        "lon": city["lon"],
                        "province": prov["name"],
                    })
            except Exception:
                continue
        if cities:
            try:
                with open(CITY_INDEX_FILE, "w", encoding="utf-8") as f:
                    json.dump({"cities": cities, "ts": time.time()}, f, ensure_ascii=False)
            except Exception:
                pass
        return cities
    finally:
        with _city_index_lock:
            _city_index_building = False


def lookup_city_in_index(city_name: str) -> Optional[Dict[str, Any]]:
    cities = _load_city_index()
    if not cities:
        # 首次用到时同步建一次索引（约 2s），之后全部走缓存
        cities = build_city_index()
    if not cities:
        # 首次用到时同步建一次索引（约 2s），之后全部走缓存
        cities = build_city_index()
    if not cities:
        return None
    key = _normalize_city(city_name)
    if not key:
        return None
    exact = [c for c in cities if _normalize_city(c["name"]) == key]
    if not exact:
        exact = [c for c in cities if key in _normalize_city(c["name"]) or _normalize_city(c["name"]) in key]
    if not exact:
        return None
    hit = exact[0]
    return {
        "city": hit["name"].replace("市", ""),
        "region": hit.get("province", "").replace("省", ""),
        "lat": hit["lat"],
        "lon": hit["lon"],
    }


def search_places(query: str, near_city: str = "", near_region: str = "") -> list:
    """地标/街道/小区检索（Photon，免 Key）。返回带坐标与所属区县的候选列表。

    区县级行政区划只到「区」，同区内的实际位置差异要靠这种更细的地理编码来定位。
    """
    query = (query or "").strip()
    if not query:
        return []
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    q = f"{query} {near_city}".strip() if near_city and near_city not in query else query
    url = f"https://photon.komoot.io/api/?q={urllib.parse.quote(q)}&limit=8"
    req = urllib.request.Request(url, headers={"User-Agent": "BigFolderLauncher-Weather/2.4"})
    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    region_key = (near_region or "").replace("省", "").replace("市", "")
    out = []
    for feat in data.get("features", []):
        prop = feat.get("properties", {}) or {}
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        state = prop.get("state", "") or ""
        # 有省份信息时只保留同省结果，避免「华阳街道」搜出镇江的
        if region_key and state and region_key not in state:
            continue
        name = prop.get("name") or prop.get("street") or prop.get("city") or ""
        district = prop.get("district") or prop.get("county") or ""
        hint = " · ".join([x for x in (district, prop.get("city") or near_city, state) if x])
        out.append({
            "name": name,
            "hint": hint,
            "lat": round(float(coords[1]), 5),
            "lon": round(float(coords[0]), 5),
            "city": prop.get("city") or near_city,
            "district": district or name,
            "region": state or near_region,
        })
    return out[:6]

def fetch_city_districts(city: str, region: str = "") -> list:
    """按城市名取区县列表（含区中心坐标）。省→市→区三级查询，结果落盘缓存。"""
    city_key = f"{region}|{city}".strip("|")
    cache = _load_areas_cache()
    if city_key in cache:
        return cache[city_key]

    city_name = city.replace("市", "").replace("地区", "")
    region_name = region.replace("省", "").replace("市", "").replace("自治区", "")
    if not region_name:
        # 只给了城市名：从全国索引里补齐省份，否则三级区划查询无从下手
        indexed = lookup_city_in_index(city)
        if indexed:
            region_name = indexed.get("region", "")
            city_name = indexed.get("city", city_name)

    provinces = _datav_areas(100000)
    province = next((p for p in provinces if region_name and region_name in p["name"]), None)
    if not province:
        # 直辖市常被当作"城市"传入（北京/上海/天津/重庆），用城市名再匹配一次省级
        province = next((p for p in provinces if city_name and city_name in p["name"]), None)
    if not province:
        return []

    # 直辖市：省级下一级就是区县，不需要再过一层"市"
    if city_name and city_name in province["name"]:
        districts = _datav_areas(int(province["adcode"]))
        cache[city_key] = districts
        _save_areas_cache(cache)
        return districts

    cities = _datav_areas(int(province["adcode"]))
    city_hit = next((c for c in cities if city_name and city_name in c["name"]), None)
    if not city_hit:
        # 省直辖县级市（如 仙桃、济源）与部分县级市直接挂在省下
        city_hit = next((c for c in cities if c["name"].replace("市", "") in city_name), None)
    if not city_hit:
        return []
    districts = _datav_areas(int(city_hit["adcode"]))
    cache[city_key] = districts
    _save_areas_cache(cache)
    return districts


def resolve_city_coordinates(city_name: str) -> Optional[Dict[str, Any]]:
    """解析城市名称，返回包含 city, region, lat, lon 的字典，支持内置库与在线地理编码"""
    raw = city_name.strip()
    if not raw:
        return None
    clean = raw.rstrip("市").rstrip("区").rstrip("县")
    # 1. 匹配内置常见城市
    for k, v in POPULAR_CITIES.items():
        if clean == k or clean in k or k in clean:
            return {"city": k, "region": v["region"], "lat": v["lat"], "lon": v["lon"]}
    # 2. 全国省市索引（首次后台构建、之后落盘缓存）
    indexed = lookup_city_in_index(clean)
    if indexed:
        return indexed

    # 3. 调用 Open-Meteo Geocoding 免 Key 检索
    try:
        quoted = urllib.parse.quote(raw)
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={quoted}&count=1&language=zh"
        req = urllib.request.Request(url, headers={"User-Agent": "BigFolderLauncher-Weather/2.4"})
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=4, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", []) or []
            # 重名地点很多（"昆山"可能是成都一个村，也可能是江苏昆山市）：
            # 先按名称相关度过滤，再取人口最大的那个，避免解析到山村。
            def name_score(item):
                nm = (item.get("name") or "").replace("市", "").replace("县", "").replace("区", "")
                return 1 if (nm == clean or clean in nm or nm in clean) else 0

            ranked = sorted(results[:6], key=lambda x: (name_score(x), x.get("population") or 0), reverse=True)
            if ranked:
                r = ranked[0]
                # 县级市/乡镇常有同名村落（"昆山"能匹配到成都、福建的村子）。
                # 没有人口数据的一律视为低置信度，宁可让用户改填地级市，也不把人定位到外省。
                if not (r.get("population") or 0) >= 20000:
                    return None
                return {
                    "city": r.get("name", raw),
                    "region": r.get("admin1", ""),
                    "lat": float(r["latitude"]),
                    "lon": float(r["longitude"])
                }
    except Exception:
        pass
    return None

# WMO Weather interpretation codes (WW) 字典映射
WMO_WEATHER_MAP = {
    0: ("晴朗", "☀️", "晴空万里，阳光明媚"),
    1: ("主要晴朗", "🌤️", "大部分晴朗，微风拂面"),
    2: ("多云", "⛅", "云层微聚，微光透映"),
    3: ("阴天", "☁️", "阴云密布，气温适宜"),
    45: ("有雾", "🌫️", "晨雾缭绕，能见度低"),
    48: ("冻雾", "🌫️", "寒霜凝结，路面湿滑"),
    51: ("微量细雨", "🌧️", "细雨霏霏，出行备伞"),
    53: ("小毛毛雨", "🌧️", "轻度降水，空气湿润"),
    55: ("较密毛毛雨", "🌧️", "密雨连绵，湿气渐浓"),
    61: ("微弱小雨", "🌧️", "轻微小雨，偶见滴答"),
    63: ("中雨", "🌧️", "细密中雨，适宜室内"),
    65: ("大雨", "🌧️", "雨势较大，出行注意"),
    71: ("微量小雪", "🌨️", "零星雪花，初冬微凉"),
    73: ("中雪", "❄️", "白雪纷飞，银装素裹"),
    75: ("大雪", "❄️", "降雪丰沛，寒气逼人"),
    77: ("雪粒", "🌨️", "冰粒细小，注意保暖"),
    80: ("微弱阵雨", "🌦️", "偶见阵雨，转瞬即晴"),
    81: ("中度阵雨", "🌦️", "过境阵雨，微风阵阵"),
    82: ("强阵雨", "⛈️", "疾风劲雨，注意防护"),
    85: ("轻度阵雪", "🌨️", "局部阵雪，气温骤降"),
    86: ("强阵雪", "❄️", "强劲阵雪，路面结冰"),
    95: ("雷雨", "⛈️", "雷鸣电闪，暴风骤雨"),
    96: ("雷雨伴有小冰雹", "⛈️", "局部强对流，谨慎外出"),
    99: ("雷雨伴有大冰雹", "⛈️", "极端强对流，安全避险"),
}


class WeatherService(QObject):
    """实时气象服务引擎 (单例、本地持久缓存、异步轮询)"""

    weather_updated = Signal(dict)
    weather_failed = Signal(str)

    CACHE_FILE = os.path.join(os.path.dirname(__file__), ".weather_cache.json")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_data: Optional[Dict[str, Any]] = None
        self._is_updating: bool = False
        self._lock = threading.Lock()
        self.custom_location: Optional[Dict[str, Any]] = None
        self.preferred_district: Optional[Dict[str, Any]] = None
        self._load_cache()

    def set_custom_location(
        self,
        city: str,
        region: str = "",
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        auto_location: bool = False,
        district: str = ""
    ):
        """设置自定义城市坐标（可带区县）或恢复自动定位。

        用户明确选了区县时，无论是否勾着自动定位，都固定到该区县坐标：
        IP 定位不再覆盖，重启后无需重新选择或手动刷新。
        """
        if district.strip() and lat is not None and lon is not None and city.strip():
            auto_location = False
        if auto_location or not city.strip():
            self.custom_location = None
            if district.strip() and lat is not None and lon is not None:
                self.preferred_district = {
                    "city": city.strip(),
                    "district": district.strip(),
                    "lat": float(lat),
                    "lon": float(lon),
                }
            else:
                self.preferred_district = None
        else:
            c_name = city.strip()
            # 若未提供坐标，尝试从内置城市或地理编码解析
            if lat is None or lon is None:
                resolved = resolve_city_coordinates(c_name)
                if resolved:
                    c_name = resolved["city"]
                    region = region or resolved.get("region", "")
                    lat = resolved["lat"]
                    lon = resolved["lon"]
                else:
                    lat = 30.6586
                    lon = 104.0648
            self.custom_location = {
                "city": c_name,
                "region": region.strip(),
                "district": district.strip(),
                "lat": float(lat),
                "lon": float(lon)
            }
            if self._last_data:
                self._last_data["city"] = c_name
                if region.strip():
                    self._last_data["region"] = region.strip()
                self._last_data["district"] = district.strip()
                self._save_cache(self._last_data)

    def _load_cache(self):
        """装载本地离线缓存，保证冷启动 0ms 瞬间上屏"""
        if os.path.exists(self.CACHE_FILE):
            try:
                with open(self.CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "temp" in data:
                        self._last_data = data
            except Exception:
                pass

    def _save_cache(self, data: Dict[str, Any]):
        try:
            with open(self.CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_cached_data(self) -> Dict[str, Any]:
        """获取当前缓存的气象数据（若无缓存返回自愈初始态）"""
        if self._last_data:
            if self.custom_location:
                self._last_data["city"] = self.custom_location.get("city", self._last_data.get("city", "成都"))
                if self.custom_location.get("region"):
                    self._last_data["region"] = self.custom_location.get("region", self._last_data.get("region", "四川"))
                self._last_data["district"] = self.custom_location.get("district", "")
            return self._last_data

        c_city = self.custom_location.get("city", "成都") if self.custom_location else "成都"
        c_reg = self.custom_location.get("region", "四川") if self.custom_location else "四川"

        return {
            "city": c_city,
            "region": c_reg,
            "temp": 14.0,
            "condition": "阴天",
            "icon": "☁️",
            "desc": "阴云密布，气温适宜",
            "temp_max": 16.0,
            "temp_min": 14.0,
            "humidity": 68,
            "wind": 4.0,
            "updated_at": "刚刚",
            "is_stale": True
        }

    def fetch_async(self, force: bool = False):
        """异步拉取最新天气 (带 15 分钟节流防刷)"""
        if self._is_updating:
            return

        now = time.time()
        if not force and self._last_data:
            last_ts = self._last_data.get("_timestamp", 0)
            if (now - last_ts) < 900:  # 15 分钟以内直接复用缓存
                self.weather_updated.emit(self._last_data)
                return

        threading.Thread(target=self._worker_fetch, daemon=True).start()

    def _load_geo(self):
        try:
            with open(GEO_CACHE_FILE, "r", encoding="utf-8") as f:
                geo = json.load(f)
            if geo.get("lat") is not None and geo.get("lon") is not None:
                return geo
        except Exception:
            pass
        return None

    def _save_geo(self, geo: Dict[str, Any]):
        try:
            with open(GEO_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(geo, f, ensure_ascii=False)
        except Exception:
            pass

    def resolve_ip_location(self, force: bool = False) -> Optional[Dict[str, Any]]:
        """按实时 IP 定位；代理/异地出口会被拒，回退到上次可信位置。

        开梯子时出口常在境外或港澳台，直接采用会把城市切到香港，所以这里做三道校验：
        1) 国家必须是中国大陆；2) 与上次可信位置距离 <= 300km；3) 解析失败沿用上次。
        force=True 时跳过校验（用户主动点「按当前 IP 重新定位」）。
        """
        prev = getattr(self, "_last_geo", None) or self._load_geo()
        self._last_geo = prev
        try:
            req = urllib.request.Request(
                "http://ip-api.com/json/?lang=zh-CN&fields=status,country,regionName,city,lat,lon,isp,query",
                headers={"User-Agent": "BigFolderLauncher-Weather/2.4"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return prev

        if data.get("status") != "success":
            return prev
        country = data.get("country") or ""
        if not force:
            if not country.startswith("中国") or any(k in country for k in ("香港", "澳门", "台湾")):
                return prev
        try:
            lat, lon = float(data.get("lat")), float(data.get("lon"))
        except Exception:
            return prev

        if prev and not force and haversine_km(prev["lat"], prev["lon"], lat, lon) > 300:
            return prev

        geo = {
            "city": data.get("city") or (prev or {}).get("city", "成都"),
            "region": data.get("regionName") or (prev or {}).get("region", "四川"),
            "lat": lat,
            "lon": lon,
            "isp": data.get("isp", ""),
            "ts": time.time(),
        }
        self._last_geo = geo
        self._save_geo(geo)
        return geo

    def _worker_fetch(self):
        with self._lock:
            self._is_updating = True

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # 1. 确定经纬度与城市名称
        if self.custom_location and self.custom_location.get("city"):
            city = self.custom_location["city"]
            region = self.custom_location.get("region", "")
            lat = self.custom_location.get("lat", 30.6586)
            lon = self.custom_location.get("lon", 104.0648)
            district = self.custom_location.get("district", "")
        else:
            # IP 自动定位
            city = "成都"
            region = "四川"
            district = ""
            lat, lon = 30.6586, 104.0648  # 默认基准
            geo = self.resolve_ip_location()
            if geo:
                city = geo.get("city") or city
                region = geo.get("region") or region
                lat = geo.get("lat", lat)
                lon = geo.get("lon", lon)
                pref = getattr(self, "preferred_district", None)
                if pref and pref.get("city") and pref["city"].replace("市", "") in city.replace("市", ""):
                    # 同城才用区县偏好，换城市时自动失效
                    district = pref["district"]
                    lat, lon = pref["lat"], pref["lon"]
            elif self._last_data:
                # 定位失败不阻断，沿用缓存里的地区
                city = self._last_data.get("city", "成都")
                region = self._last_data.get("region", "四川")
                district = self._last_data.get("district", "")

        # 2. 主预报：国内点位优先走中国气象局 GRAPES 全球模型（约 9km 网格），
        #    比默认 best_match 更贴合本地实况；模型不可用时自动回退。
        common = (
            f"latitude={lat}&longitude={lon}&timezone=auto"
            f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,"
            f"wind_speed_10m,wind_direction_10m,precipitation,cloud_cover"
            f"&hourly=temperature_2m,precipitation_probability,apparent_temperature,weather_code"
            f"&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
            f"uv_index_max,sunrise,sunset&forecast_days=3&forecast_hours=12"
        )
        raw_weather, last_err, used_model = None, None, ""
        for model, candidate in (("cma_grapes_global",
                                  f"https://api.open-meteo.com/v1/forecast?{common}&models=cma_grapes_global"),
                                 ("best_match", f"https://api.open-meteo.com/v1/forecast?{common}")):
            try:
                req = urllib.request.Request(candidate, headers={"User-Agent": "BigFolderLauncher-Weather/2.4"})
                with urllib.request.urlopen(req, timeout=6, context=ctx) as resp:
                    raw_weather = json.loads(resp.read().decode("utf-8"))
                used_model = model
                break
            except Exception as exc:
                last_err = exc
        if raw_weather is None:
            # 上游 503/超时属于常态：保留上次数据，不要把异常抛到线程外
            self.weather_failed.emit(str(last_err or "weather fetch failed"))
            with self._lock:
                self._is_updating = False
            return

        try:
                curr = raw_weather.get("current", {})
                daily = raw_weather.get("daily", {})
                hourly = raw_weather.get("hourly", {})

                temp = round(curr.get("temperature_2m", 22.0), 1)
                humidity = int(curr.get("relative_humidity_2m", 60))
                wind = round(curr.get("wind_speed_10m", 10.0), 1)
                code = int(curr.get("weather_code", 0))
                feels = round(curr.get("apparent_temperature", temp), 1)

                t_max = round(daily.get("temperature_2m_max", [temp + 3])[0], 1) if daily.get("temperature_2m_max") else round(temp + 3, 1)
                t_min = round(daily.get("temperature_2m_min", [temp - 4])[0], 1) if daily.get("temperature_2m_min") else round(temp - 4, 1)

                # 当前小时降水概率：hourly 时间序列按当前整点对齐
                prob_list = hourly.get("precipitation_probability") or []
                hourly_times = hourly.get("time") or []
                precip_prob = None
                try:
                    now_key = time.strftime("%Y-%m-%dT%H:00")
                    if now_key in hourly_times:
                        precip_prob = prob_list[hourly_times.index(now_key)]
                    elif prob_list:
                        precip_prob = prob_list[0]
                except Exception:
                    precip_prob = prob_list[0] if prob_list else None

                # 未来 12 小时逐时预报，供抽屉/悬浮详情使用
                hours = []
                for i, ts in enumerate((hourly.get("time") or [])[:12]):
                    try:
                        hours.append({
                            "t": ts[11:16],
                            "temp": round(hourly["temperature_2m"][i], 1),
                            "prob": hourly.get("precipitation_probability", [None] * (i + 1))[i],
                            "code": int(hourly.get("weather_code", [0] * (i + 1))[i] or 0),
                        })
                    except Exception:
                        continue

                days = []
                for i, ds in enumerate((daily.get("time") or [])[:3]):
                    try:
                        days.append({
                            "date": ds,
                            "max": round(daily["temperature_2m_max"][i], 1),
                            "min": round(daily["temperature_2m_min"][i], 1),
                            "prob": (daily.get("precipitation_probability_max") or [None] * (i + 1))[i],
                            "code": int((daily.get("weather_code") or [0] * (i + 1))[i] or 0),
                        })
                    except Exception:
                        continue

                uv = (daily.get("uv_index_max") or [None])[0]
                sunrise = (daily.get("sunrise") or [""])[0][11:16]
                sunset = (daily.get("sunset") or [""])[0][11:16]

                # CMA 模型不提供降水概率与紫外线，用默认模型补一次概率数据再回填
                if precip_prob is None or uv is None:
                    try:
                        sup_url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                                   f"&hourly=precipitation_probability&daily=uv_index_max,precipitation_probability_max"
                                   f"&timezone=auto&forecast_days=3&forecast_hours=12")
                        sup_req = urllib.request.Request(sup_url, headers={"User-Agent": "BigFolderLauncher-Weather/2.4"})
                        with urllib.request.urlopen(sup_req, timeout=5, context=ctx) as sup_resp:
                            sup = json.loads(sup_resp.read().decode("utf-8"))
                        sup_hourly = sup.get("hourly", {})
                        sup_prob = sup_hourly.get("precipitation_probability") or []
                        sup_times = sup_hourly.get("time") or []
                        if precip_prob is None and sup_prob:
                            now_key = time.strftime("%Y-%m-%dT%H:00")
                            precip_prob = sup_prob[sup_times.index(now_key)] if now_key in sup_times else sup_prob[0]
                        for i, hour in enumerate(hours):
                            if hour.get("prob") is None and i < len(sup_prob):
                                hour["prob"] = sup_prob[i]
                        sup_daily = sup.get("daily", {})
                        if uv is None and sup_daily.get("uv_index_max"):
                            uv = sup_daily["uv_index_max"][0]
                        sup_day_prob = sup_daily.get("precipitation_probability_max") or []
                        for i, day in enumerate(days):
                            if day.get("prob") is None and i < len(sup_day_prob):
                                day["prob"] = sup_day_prob[i]
                    except Exception:
                        pass

                # 3. 真实空气质量（原卡片写死的「空气优」是假数据）
                aqi, aqi_level = None, ""
                try:
                    aq_url = (f"https://air-quality-api.open-meteo.com/v1/air-quality?"
                              f"latitude={lat}&longitude={lon}&current=us_aqi&timezone=auto")
                    aq_req = urllib.request.Request(aq_url, headers={"User-Agent": "BigFolderLauncher-Weather/2.4"})
                    with urllib.request.urlopen(aq_req, timeout=4, context=ctx) as aq_resp:
                        aq = json.loads(aq_resp.read().decode("utf-8")).get("current", {})
                    if aq.get("us_aqi") is not None:
                        aqi = int(round(aq["us_aqi"]))
                        aqi_level = ("优" if aqi <= 50 else "良" if aqi <= 100 else
                                     "轻度污染" if aqi <= 150 else "中度污染" if aqi <= 200 else
                                     "重度污染" if aqi <= 300 else "严重污染")
                except Exception:
                    pass

                cond_tuple = WMO_WEATHER_MAP.get(code, ("多云", "⛅", "天气平稳"))
                cond_text, icon, desc = cond_tuple

                payload = {
                    "city": city,
                    "region": region,
                    "district": district,
                    "temp": temp,
                    "feels_like": feels,
                    "condition": cond_text,
                    "icon": icon,
                    "desc": desc,
                    "temp_max": t_max,
                    "temp_min": t_min,
                    "humidity": humidity,
                    "wind": wind,
                    "precip_prob": precip_prob,
                    "aqi": aqi,
                    "aqi_level": aqi_level,
                    "uv": uv,
                    "sunrise": sunrise,
                    "sunset": sunset,
                    "hours": hours,
                    "days": days,
                    "model": used_model,
                    "updated_at": time.strftime("%H:%M"),
                    "_timestamp": time.time(),
                    "is_stale": False
                }

                self._last_data = payload
                self._save_cache(payload)
                self.weather_updated.emit(payload)
        except Exception as e:
            self.weather_failed.emit(str(e))
        finally:
            with self._lock:
                self._is_updating = False


# 全局单例
_weather_service_instance: Optional[WeatherService] = None

def get_weather_service() -> WeatherService:
    global _weather_service_instance
    if _weather_service_instance is None:
        _weather_service_instance = WeatherService()
    return _weather_service_instance
