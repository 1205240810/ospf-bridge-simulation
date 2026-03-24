import telnetlib
import time

def send_router_cmds(name, port, cmds, delay=0.5, needs_break=False):
    """【终极除错版】带 \r\n 唤醒、Ctrl+C 打断与闭环探测"""
    print(f"🔄 正在尝试连接网络设备 {name} (127.0.0.1:{port})...")
    try:
        tn = telnetlib.Telnet("127.0.0.1", port, timeout=10)
        
        print(f"   ⏳ 正在探测 {name} 命令行是否就绪...")
        is_ready = False
        for i in range(40):
            # 如果是 H3C 交换机，每次探测前先发一个 Ctrl+C (\x03) 强行打断 Auto-Config
            if needs_break:
                tn.write(b"\x03")
                time.sleep(0.2)
                
            tn.write(b"\r\n")  # 发送回车换行
            time.sleep(1.5)
            
            output = tn.read_very_eager().decode('ascii', errors='ignore')
            
            # 只要屏幕上出现提示符，说明进入了命令行
            if '<' in output or '[' in output or '>' in output:
                is_ready = True
                print(f"   🟢 探测成功！{name} 命令行已就绪。")
                break
                
        if not is_ready:
            print(f"   ❌ 失败: 探测彻底超时！请确认设备是否开机完毕。")
            tn.close()
            return

        print(f"🚀 开始为 {name} 注入配置...")
        for cmd in cmds:
            tn.write(cmd.encode('ascii') + b"\r\n")
            time.sleep(delay)
            
        tn.close()
        print(f"✅ {name} 配置注入完成！\n")
    except Exception as e:
        print(f"❌ 失败: 连接 {name} 时发生错误: {e}\n")

def send_pc_cmds(name, port, cmds, username="cirros", password="gocubsgo"):
    """【PC终端专用】严格阻塞式登录，杜绝吞命令"""
    print(f"🔄 正在尝试连接 PC {name} (127.0.0.1:{port})...")
    try:
        tn = telnetlib.Telnet("127.0.0.1", port, timeout=5)
        
        # 1. 敲回车探路，看看当前是不是已经登录了
        tn.write(b"\n")
        time.sleep(1)
        out = tn.read_very_eager()
        
        if b"$" in out or b"#" in out:
            print(f"   🟢 {name} 已经是登录状态，直接下发配置！")
        else:
            print(f"   ⏳ {name} 未登录，开始严格执行登录流程...")
            # 等待 login
            tn.write(b"\n")
            tn.read_until(b"login: ", timeout=3)
            print("   🔑 敲入账号...")
            tn.write(username.encode('ascii') + b"\n")
            
            # 等待 Password
            tn.read_until(b"Password: ", timeout=3)
            print("   🔑 敲入密码...")
            tn.write(password.encode('ascii') + b"\n")
            
            # 【核心修复点】：死等 $ 提示符出现，绝不抢跑！
            print("   ⏳ 正在等待系统 Shell 加载...")
            res = tn.read_until(b"$ ", timeout=5)
            
            if b"$" not in res:
                # 如果等了 5 秒还没看到 $，直接报错并打印出它到底卡在了哪
                print(f"   ❌ 登录失败！系统底层回显如下:\n{res.decode('ascii', errors='ignore')}")
                tn.close()
                return
            print(f"   🟢 {name} 登录完全成功！进入 Shell。")

        # 拿到 Shell 后，再从容不迫地下发配置
        print(f"🚀 开始为 {name} 注入配置...")
        for cmd in cmds:
            tn.write(cmd.encode('ascii') + b"\n")
            time.sleep(0.3)  # 每条命令稳稳地停顿一下
            
        tn.close()
        print(f"✅ {name} 配置注入完成！\n")
    except Exception as e:
        print(f"❌ 失败: 连接 {name} 时发生错误: {e}\n")

def run_automation():
    # PC1 终极配置序列
    # PC1 终极熬鹰序列
    # PC1 潜行版配置（不惊动守护进程）
    pc1_cmds = [
        "sudo ip addr add 10.1.1.2/24 dev eth0",   
        "sudo ip route del default",              
        "sudo ip route add default via 10.1.1.1"   
    ]
    
    # PC2 潜行版配置
    pc2_cmds = [
        "sudo ip addr add 30.1.1.2/24 dev eth0",
        "sudo ip route del default",
        "sudo ip route add default via 30.1.1.1"
    ]
    # 交换机命令 (H3C)
    s1_cmds = [
        "system-view", "sysname S1", "vlan 10", "quit",
        "interface range HGE1/0/1 to HGE1/0/2",
        "port link-mode bridge", "port link-type access", "port access vlan 10",
        "undo shutdown", "quit", "return"
    ]
    s2_cmds = [
        "system-view", "sysname S2", "vlan 30", "quit",
        "interface range HGE1/0/1 to HGE1/0/2",
        "port link-mode bridge", "port link-type access", "port access vlan 30",
        "undo shutdown", "quit", "return"
    ]

    # 路由器命令 (华为)
    r1_cmds = [
        "system-view", "interface Ethernet1/0/0", "ip address 10.1.1.1 24", "commit", "quit",
        "interface Ethernet1/0/1", "ip address 20.1.1.2 24", "commit", "quit",
        "interface Ethernet1/0/2", "ip address 192.168.153.200 24", "commit", "quit",
        "ospf 1", "area 0", "network 10.1.1.0 0.0.0.255", "network 20.1.1.0 0.0.0.255", 
        "network 192.168.153.0 0.0.0.255", "commit", "return"
    ]
    r3_cmds = [
        "system-view", "interface Ethernet1/0/0", "ip address 20.1.1.1 24", "commit", "quit",
        "interface Ethernet1/0/1", "ip address 21.1.1.1 24", "commit", "quit",
        "ospf 1", "area 0", "network 20.1.1.0 0.0.0.255", "quit",
        "area 1", "network 21.1.1.0 0.0.0.255", "commit", "return"
    ]
    r2_cmds = [
        "system-view", "interface Ethernet1/0/0", "ip address 30.1.1.1 24", "commit", "quit",
        "interface Ethernet1/0/1", "ip address 21.1.1.2 24", "commit", "quit",
        "ospf 1", "area 1", "network 30.1.1.0 0.0.0.255", "network 21.1.1.0 0.0.0.255", 
        "commit", "return"
    ]

    # ==========================================
    # 执行队列
    # ==========================================
    send_pc_cmds("PC1", 5004, pc1_cmds)
    send_pc_cmds("PC2", 5005, pc2_cmds)
    
    # 【关键修改】给 S1 和 S2 加上 needs_break=True 参数，让机器人替你狂按 Ctrl+C
    send_router_cmds("S1", 6001, s1_cmds, delay=0.5, needs_break=True)
    send_router_cmds("S2", 6002, s2_cmds, delay=0.5, needs_break=True)
    
    # 路由器不需要打断，保持默认
    send_router_cmds("R1", 5001, r1_cmds, delay=1.0)
    send_router_cmds("R3", 5003, r3_cmds, delay=1.0)
    send_router_cmds("R2", 5002, r2_cmds, delay=1.0)

if __name__ == "__main__":
    print("⏳ 准备开始全网自动化配置...")
    run_automation()
    print("🎉 全网配置下发完毕！请等待 OSPF 邻居建立 (约需 10-20 秒)")