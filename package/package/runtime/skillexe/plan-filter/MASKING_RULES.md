# 脱敏规则文档

## 密码脱敏规则

### 长度 < 6位
- **规则**: 替换为 `****`
- **示例**: 
  - `password=abc` → `password=****`
  - `password=abc12` → `password=****`

### 长度 6-12位
- **规则**: 保留前4位 + `****`
- **示例**:
  - `password=abcdef12` → `password=abcd****`
  - `password=12345678` → `password=1234****`

### 长度 > 12位
- **规则**: 保留前6位 + `...` + 后4位
- **示例**:
  - `password=abcdefgh123456` → `password=abcdef...3456`
  - `password=mysecretpassword123` → `password=mysecr...d123`

## API Key 脱敏规则

### 固定格式
- **规则**: 保留前6位 + `...` + 后4位
- **示例**:
  - `mv_9bebd4e14727c1d0106e6b7103610c8834e7963c` → `mv_9bebd...e7963c`
  - `api_key=abcdefghijklmnopqrstuvwxyz` → `api_key=abcdef...wxyz`

## Token 脱敏规则

### JWT Token
- **规则**: 保留前6位 + `...` + 后4位
- **示例**:
  - `Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...` → `Bearer eyJhbG...pXVCJ9`

## 敏感信息类型

| 类型 | 匹配模式 | 脱敏规则 |
|------|----------|----------|
| API Key | `api_key`, `apikey`, `mv_`前缀 | 前6位+...+后4位 |
| 密码 | `password`, `passwd`, `pwd` | <6位:****, 6-12位:前4+****, >12位:前6+...+后4 |
| Token | `Bearer`, `token` | 前6位+...+后4位 |
| 密钥 | `secret_key`, `private_key` | 前6位+...+后4位 |
| 加密密钥 | `encryption_key` | 前6位+...+后4位 |

## 使用示例

```bash
# 基本过滤
/PF filter "password=abc12"
# 输出: password=****

# 显示详情
/PF filter --details "password=abcdefgh123456"
# 输出: password=abcdef...3456
# [INFO] 检测到 1 处敏感信息已脱敏
```
