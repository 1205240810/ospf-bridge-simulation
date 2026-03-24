import sys
import yaml
import time
from devices import NE40Router, H3CSwitch, CirrosPC
from network import create_veth_link
import subprocess
import os

def cleanup_environment():
    """
    【OVS 专用清场】
    """
    print("🧹 正在清理旧环境 (Process & OVS)...")
    
    # 1. 杀进程
    subprocess.run("sudo killall -9 qemu-system-x86_64 2>/dev/null", shell=True)
    time.sleep(1)
    
    # 2. 清理 OVS 网桥 
    cmd_ovs = "sudo ovs-vsctl list-br | grep 'br_' | xargs -I {} sudo ovs-vsctl del-br {}"
    subprocess.run(cmd_ovs, shell=True)

    # 3. 精确清理 veth 和 tap
    print("   - 清理残留 veth 接口...")
    clean_veth_cmd = (
        "sudo ip -o link show type veth "
        "| awk -F': ' '{print $2}' "
        "| awk -F'@' '{print $1}' "
        "| xargs -I {} sudo ip link del {} 2>/dev/null"
    )
    subprocess.run(clean_veth_cmd, shell=True)

    print("   - 清理残留 tap 接口...")
    clean_tap_cmd = (
        "sudo ip link | grep 'tap_' "
        "| awk -F': ' '{print $2}' "
        "| awk -F'@' '{print $1}' " 
        "| xargs -I {} sudo ip link del {} 2>/dev/null"
    )
    subprocess.run(clean_tap_cmd, shell=True)
    
    print("✅ 环境已清理完毕")

def load_topology(yaml_file):
    with open(yaml_file, 'r') as f:
        topo = yaml.safe_load(f)

    proj_name = topo.get('project_name', 'default_lab')
    workspace_path = os.path.join("./workspaces", proj_name)

    print(f"本次实验工作区: {workspace_path}")

    device_map = {}

    # 1. 启动所有设备
    for dev_conf in topo['devices']:
        name = dev_conf['name']
        port = dev_conf.get('console_port')
        dtype = dev_conf['type']
        
        img_path = dev_conf.get('image') 

        device = None 

        if dtype == 'ne40':
            if img_path:
                device = NE40Router(name, console_port=port, workspace_dir=workspace_path, image_path=img_path)
            else:
                device = NE40Router(name, console_port=port, workspace_dir=workspace_path)

        elif dtype == 'h3c':  
            if img_path:
                device = H3CSwitch(name, console_port=port, workspace_dir=workspace_path, image_path=img_path)
            else:
                device = H3CSwitch(name, console_port=port, workspace_dir=workspace_path, image_path=img_path)
                
        elif dtype == 'pc':
            if img_path:
                device = CirrosPC(name, console_port=port, workspace_dir=workspace_path, image_path=img_path)
            else:
                device = CirrosPC(name, console_port=port, workspace_dir=workspace_path)
                
        elif dtype == 'cloud':
            print(f"☁️ 注册云节点: {name} (物理桥接模式)")
            device = "CLOUD_NODE"  

        if device:
            if device != "CLOUD_NODE":  
                device.start()
            device_map[name] = device
        else:
            print(f"⚠️ 未知设备类型: {dtype}，跳过...")

    print("等待设备启动...")
    time.sleep(2)

    # 2. 建立连接
    print("\nStarting Link Setup...")
    for link_data in topo['links']:
        
        # === 新增：解析字典或列表，实现向下兼容 ===
        if isinstance(link_data, dict):
            # 新版本带有 emulation 的格式
            link = link_data["endpoints"]
            emulation = link_data.get("emulation", None)
        else:
            # 老版本纯列表格式
            link = link_data
            emulation = None

        # === 情况 A: PC 连 路由 (3个参数) ===
        if len(link) == 3:
            pc_name = link[0]
            router_name = link[1]
            port_idx = link[2] 

            print(f"Connecting PC: {pc_name} -> {router_name} Port {port_idx}")
            pc = device_map[pc_name]
            router = device_map[router_name]
            
            if emulation:
                print(f"⚠️ 提示: PC终端连线暂未开启仿真参数下发，当前 {emulation} 设置将被忽略。")
                
            pc.connect_to_router(router, port_idx)

        # === 情况 B: 路由连路由 或 路由连云 (4个参数) ===
        elif len(link) == 4:
            if link[2] == "CLOUD" or link[0] == "CLOUD":
                if link[2] == "CLOUD":
                    router_name, router_port, cloud_name, phys_nic = link
                else:
                    cloud_name, phys_nic, router_name, router_port = link

                print(f"Connecting Router: {router_name} Port {router_port} <--> {cloud_name} NIC {phys_nic}")
                
                router = device_map[router_name]
                bridge_A = router.ports[router_port - 1]['bridge']
                
                if emulation:
                    print(f"⚠️ 提示: 物理网卡桥接暂不支持注入仿真参数，当前 {emulation} 设置将被忽略。")
                
                subprocess.run(f"sudo ip link set {phys_nic} up", shell=True)
                subprocess.run(f"sudo ovs-vsctl add-port {bridge_A.name} {phys_nic}", shell=True)
                print(f"✅ 成功将物理网卡 {phys_nic} 桥接到 {bridge_A.name}")

            else:
                rA_name = link[0]
                rA_port = link[1]
                rB_name = link[2]
                rB_port = link[3]

                print(f"Connecting Router: {rA_name} Port {rA_port} <--> {rB_name} Port {rB_port}")
                
                rA = device_map[rA_name]
                rB = device_map[rB_name]

                bridge_A = rA.ports[rA_port - 1]['bridge']
                bridge_B = rB.ports[rB_port - 1]['bridge']

                # === 核心修改：将 emulation 参数传递给底层连线函数 ===
                create_veth_link(bridge_A.name, bridge_B.name, emulation_params=emulation)

        else:
            print(f"⚠️ 无法识别的连接格式: {link}")

    return device_map

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法错误！请使用以下格式:")
        print("  python main.py load <yaml_file>")
        print("  python main.py clean")
        exit(1)

    action = sys.argv[1]

    if action == "load":
        if len(sys.argv) < 3:
            print("❌ 错误: 请指定要加载的 topology 文件路径。")
            exit(1)
            
        yaml_file = sys.argv[2]

        cleanup_environment() 
        print(f"正在加载拓扑: {yaml_file}")
        devices = load_topology(yaml_file)
        
        print("\n✅ 环境运行中... (按 Ctrl+C 退出)")
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            print("停止所有设备...")
            for dev in devices.values():
                dev.stop()
    
    elif action == "clean":
        cleanup_environment()
        print("✅ 手动清理完成。")

    else:
        print(f"❌ 未知命令: {action}")