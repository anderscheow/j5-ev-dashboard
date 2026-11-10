# 配置与已知车型

[English](../configuration.md) · [Bahasa Indonesia](../id/configuration.md) · **简体中文** · [Bahasa Melayu](../ms/configuration.md)

仪表盘登录页和 `tools/setup.py` 会自动填写大部分设置。本参考说明可选字段和已知车型的默认值。

## `creds.json` 参考

| 键 | 必填 | 说明 |
| --- | --- | --- |
| `email`, `password` | ✅ | 你的 CarLinko 登录信息（经 TLS 以明文传输；仅保存在本地） |
| `region` | | API 区域，默认 `sea`（东南亚）。**区域错误会导致登录失败**——在欧洲/中东/非洲注册的账号需要 `emea`（以色列的 J5 Noble 曾报告此问题，[#6](https://github.com/GodrezJr2/j5-ev-dashboard/issues/6)）。如果密码正确却被拒绝，请先尝试其他区域，再排查别的原因 |
| `vehicle_id`, `device_sn` | 自动 | 车辆 ID 和设备序列号——**`setup.py` 会替你填写** |
| `vehicle` | 自动 | `{plate, model, vin}`——自动识别；界面默认隐藏车牌和 VIN |
| `battery_kwh`, `wltp_kwh_100` | | 可用电池容量和 WLTP 参考值。**车辆不会上报电池容量**，因此取值来自你本人或[已知车型表](#已知车型)；未识别的车型会在设置时询问，并在你设定之前于界面中标注为*假定值*。它会缩放所有 kWh、费用和能耗数据 |
| `currency` | | 非印尼盾国家使用的 `{symbol, locale, code}`，例如 `{"symbol":"R","locale":"en-ZA","code":"ZAR"}`。默认为 `Rp` / `id-ID` |
| `tariff`, `petrol_price`, `petrol_kml` | | 你当地的充电电价（每 kWh）、汽油价格（每升）和油耗，以你的货币计 |
| `tyre_unit` | | 轮胎显示单位：`psi`（默认）、`bar` 或 `kpa` |
| `tpms_scale` | | 轮胎原始字节 → kPa 的换算系数。J5 为间接式（没有真实 PSI）；会上报真实胎压的车型需要重新校准（见下文） |
| `car_image` | 自动 | 覆盖仪表盘主图。通常无需设置——`setup.py` 会保存 CarLinko 托管的*你这辆车*的渲染图（车型、颜色都正确），服务器会把它缓存在 `/car-photo`。只有想使用自己的照片时，才把它设为 `web/` 中的文件名或完整 URL |
| `specs` | | **Specifications** 卡片所用的宣传册数据——`{label, source, performance:[[name,value,unit]], dimensions:[…]}`。只内置了 J5 的数据；其他车型会隐藏该卡片，而不是显示别的车的数据，所以想显示就自己填写 |
| `powertrain` | | `auto`（默认）、`bev` 或 `phev`。`auto` 下，车辆一旦上报油箱就按 PHEV 处理——BEV 从不上报油箱，所以除非确有需要，此项保持关闭 |
| `chemistry` | | `lfp`（默认）或 `nmc`——决定电池养护建议 |
| `full_charge_days` | | 建议多久充一次 100 %。LFP 默认 `7`，NMC 默认 `90` |
| `gmaps_key` | | Google Maps 密钥——启用行程规划器和 SPKLU 地图（否则回退到 OSM） |
| `dashboard_password` | | 设置后（登录页 → Advanced）用密码锁定仪表盘——**如果该地址可从互联网访问，请务必设置** |

> **不在印尼？`setup.py` 现在会询问。** 它提供印尼、南非、英国和欧元区的预设（也可输入其他货币），然后询问充电电价、汽油价格和轮胎单位——这样仪表盘上所有费用都以你的货币显示，无需手动编辑 `creds.json`。随时可重新运行 `python setup.py` 修改，它会保留你已有的值。行程规划器和充电地图仍仅限印尼（使用 SPKLU/PLN 数据）。
>
> 内置价格只是*起点*，会过时——印尼默认值为 Pertamax Rp16,250/L 和 PLN SPKLU Rp2,540/kWh（2026 年 7 月）。请把 `petrol_price` / `tariff` 设为你那里的价格。

> **`tariff_idr` 是 `tariff` 的旧名称**——仍会被读取，所以现有配置继续有效，但既然费用不再仅限印尼，建议改用 `tariff`。
>
> **胎压现在会自动校准。** CarLinko 公布了各车型的轮胎公式（`appKpaFormula`，例如 `data * 1.373`），所以 `setup.py` 直接从 API 读取你这辆车的换算系数——无需手动校准。如果你已自行设置 `tpms_scale`，设置程序会保留你的值，只提示 CarLinko 给出的值；删除该键即可采用 CarLinko 的值。用 `tyre_unit`（`psi`/`bar`/`kpa`）选择显示单位。
>
> 如果数值仍然不对，轮胎卡片会显示**原始字节**——从你车辆自己的屏幕上读取一个车轮的数值，然后 `tpms_scale = 你读到的 kPa / 原始字节`。请注意，部分车型（包括 J5）使用*间接式* TPMS，只上报轮胎**状态**，从不上报真实胎压。

## 已知车型

其他车主已确认的设置。欢迎通过[兼容性报告](https://github.com/GodrezJr2/j5-ev-dashboard/issues/new?template=compatibility.md)添加你的车。

这些设置会根据识别到的车型**自动应用**——表格位于 [`tools/known_cars.py`](../../tools/known_cars.py)，你在 `creds.json` 中设置的内容仍然优先。

| 车型 | `battery_kwh` | `tpms_scale` | `tyre_unit` | 备注 |
| --- | --- | --- | --- | --- |
| Jaecoo J5 EV (ID) | `58.9` | n/a | `psi` | 参考车型。*间接式* TPMS——只上报轮胎**状态**，从不上报胎压 |
| Chery Tiggo 8 PHEV 2025 (ZA) | `18.3` | `1.779` | `bar` | 直接式 TPMS（原始值 `136` = 2.42 bar）。PHEV——油量、油耗和燃油续航均已解码（[#2](https://github.com/GodrezJr2/j5-ev-dashboard/issues/2)） |
| Chery Tiggo 7 PHEV (MY) | `18.3` | `1.779` | `psi` | 电池包和胎压换算与 Tiggo 8 PHEV 相同，已独立确认（[#3](https://github.com/GodrezJr2/j5-ev-dashboard/issues/3)）。马来西亚将其标为 **`TIGGO 7 CSH`**——自动匹配 |
| Omoda E5 2025 (UY) | `61` | — | — | LFP，WLTP 15.5 kWh/100 km——车主已确认（[#5](https://github.com/GodrezJr2/j5-ev-dashboard/issues/5)）。登录、自动识别和充电等一切正常 |

**如果你的车不在列表中**，不会替你编造任何数据：`setup.py` 会询问电池容量，在你设置之前电池卡片会标注为*假定值*，规格卡片也会保持隐藏，而不是显示别的车的宣传册数据。

**PHEV。** 油箱 %（字节 21）、油耗 L/100 km（字节 53）和**燃油续航**（字节 70–71）已解码，并显示在电池旁边。这些字节在每一帧 BEV 数据中都读作 `0`，因此 BEV 车主看不到燃油界面。`range_km` 严格保持为**仅纯电**，在 PHEV 上标注为 *EV range*；车辆自己仪表盘显示的综合续航**并未传输**——它恰好等于纯电 + 燃油（已在三次抓包中验证），所以应用自行计算并如实说明，而不是冒充成车辆给出的数字。由于 PHEV 也会用汽油行驶，其 kWh/100 km 和相对汽油的节省金额只是近似值。
