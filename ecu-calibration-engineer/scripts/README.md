# Scripts

当前 Skill 不包含自动化脚本。

后续如果需要，可以在此目录加入以下工具：

- WinOLS/CSV Map 表格读取和对比脚本。
- 原厂 Map 与修改 Map 差异矩阵生成脚本。
- HP Tuners / CSV LOG 自动摘要脚本。
- 标定 changelog 自动生成脚本。
- 文件命名和版本检查脚本。

添加脚本时必须保留以下原则：

1. 不直接覆盖原厂 ori / stock / backup 文件。
2. 默认输出到新文件或报告文件。
3. 修改 ECU/TCU 二进制文件前必须生成修改计划并等待用户确认。
4. 对实际写入文件保持 checksum、版本和 changelog 可追踪。
