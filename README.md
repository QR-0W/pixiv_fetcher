# Pixiv OAuth 图片插件

基于 pixivpy 官方库实现的 Pixiv 图片获取插件，支持 MaiBot 框架。

## 功能特性

- 标签 AND/OR 组合搜索
- 关键词搜索
- 用户搜索（按用户名或 UID）
- 作品 ID 搜索
- 日期搜索
- 长宽比筛选（横图/竖图/方图/自定义比例）
- AI 作品排除
- R18/R18G 内容控制
- 自动 Token 刷新机制
- 内置代理支持

## 安装依赖

```bash
pip install pixivpy3
```

## 配置说明

编辑 `config..toml` 文件：

```toml
[plugin]
enabled = true
config_version = "2.0.0"

[oauth]
# Pixiv OAuth Access Token（可选，有则填入，无则留空）
access_token = ""

# Pixiv OAuth Refresh Token（必需，需自行申请）
refresh_token = "你的refresh_token"

[features]
# 默认返回图片数量（1-20）
default_num = 1

# 命令冷却时间（秒）
cooldown_seconds = 10

# 是否允许R18内容
allow_r18 = true

# 是否允许R18G内容
allow_r18g = false

# 是否默认排除AI作品
default_exclude_ai = true

# 是否使用合并转发消息发送多张图片
use_forward_message = true

# 零结果时是否自动降级过滤条件
enable_auto_degradation = true

# 代理服务器地址（如需代理访问 Pixiv）
proxy = ""

# 图片代理服务器（用于转换 Pixiv 原始图片 URL）
image_proxy = "i.pixiv.cat"
```

### 获取 Refresh Token

1. 前往 [Pixiv 开发者中心](https://www.pixiv.net/developers/) 注册应用
2. 获取 OAuth 授权并获取 `refresh_token`
3. 将得到的 `refresh_token` 填入配置文件的 `refresh_token` 字段

## 使用示例

### 基础命令

```
/pixiv              获取1张随机图片
/pixiv help         显示帮助信息
/pixiv random:3     随机获取3个帖子的所有图片
```

### 标签搜索

```
/pixiv tag:萝莉                搜索"萝莉"标签
/pixiv tag:白丝|黑丝            OR搜索（白丝或黑丝）
/pixiv tag:萝莉&白丝           AND搜索（萝莉且白丝）
```

### 关键词搜索

```
/pixiv 原神                直接搜索"原神"关键词
/pixiv keyword:初音未来    显式指定关键词搜索
```

### 用户搜索

```
/pixiv user:gomzi      搜索用户名为"gomzi"的作品
/pixiv uid:12345       按用户ID搜索
```

### 作品ID搜索

```
/pixiv id:12345678     获取指定ID的作品
```

### 日期搜索

```
/pixiv date:2016-07-15    获取指定日期的作品
```

### 长宽比筛选

```
/pixiv 横图         横图 (长宽比>1)
/pixiv 竖图         竖图 (长宽比<1)
/pixiv 方图         方图 (长宽比=1)
/pixiv gt1.5        自定义长宽比大于1.5
```

### 其他选项

```
/pixiv noai         排除AI作品
/pixiv r18          R18内容 (需配置允许)
```

### 组合使用

```
/pixiv random:5 tag:萝莉|白丝 横图 noai
/pixiv 原神 tag:胡桃 5 r18
/pixiv user:gomzi 3 竖图
```

## 许可证

MIT License