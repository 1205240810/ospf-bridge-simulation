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
    # 逻辑：
    # ip -o link show type veth : 只显示 veth 类型的接口，-o 表示单行显示
    # awk -F': ' '{print $2}'   : 提取 "veth_xxx@peer" 部分
    # awk -F'@' '{print $1}'    : 只要 "@" 前面的部分，去掉后缀
    # xargs ... ip link del     : 执行删除
    
    print("   - 清理残留 veth 接口...")
    clean_veth_cmd = (
        "sudo ip -o link show type veth "
        "| awk -F': ' '{print $2}' "
        "| awk -F'@' '{print $1}' "
        "| xargs -I {} sudo ip link del {} 2>/dev/null"
    )
    subprocess.run(clean_veth_cmd, shell=True)

    print("   - 清理残留 tap 接口...")
    # 这里的 awk 逻辑稍微简单点，因为 tap 一般没有 @后缀
    clean_tap_cmd = (
        "sudo ip link | grep 'tap_' "
        "| awk -F': ' '{print $2}' "
        "| awk -F'@' '{print $1}' "  # 以防万一也有后缀
        "| xargs -I {} sudo ip link del {} 2>/dev/null"
    )
    subprocess.run(clean_tap_cmd, shell=True)
    
    print("✅ 环境已清理完毕")

def load_topology(yaml_file):
    with open(yaml_file, 'r') as f:
        topo = yaml.safe_load(f)

    # 获取项目名，如果没有就叫 default_lab
    proj_name = topo.get('project_name', 'default_lab')
    
    # 定义所有实验文件的总目录，比如叫 workspaces
    # 最终结构: ./workspaces/ospf_lab_01/
    workspace_path = os.path.join("./workspaces", proj_name)

    print(f"本次实验工作区: {workspace_path}")

    device_map = {}

    # 1. 启动所有设备
    for dev_conf in topo['devices']:
        name = dev_conf['name']
        port = dev_conf.get('console_port')
        dtype = dev_conf['type']
        
        img_path = dev_conf.get('image') 

        device = None # 初始化变量

        if dtype == 'ne40':
            if img_path:
                # 如果 YAML 里写了路径，就用 YAML 的
                device = NE40Router(name, console_port=port, workspace_dir=workspace_path, image_path=img_path)
            else:
                # 如果 YAML 没写，就用类里定义的默认值
                device = NE40Router(name, console_port=port, workspace_dir=workspace_path)

        elif dtype == 'h3c':  # <--- 新增这个判断
            if img_path:
                device = H3CSwitch(name, console_port=port, workspace_dir=workspace_path, image_path=img_path)
            else:
                device = H3CSwitch(name, console_port=port, workspace_dir=workspace_path, image_path=img_path)
                
        elif dtype == 'pc':
            if img_path:
                device = CirrosPC(name, console_port=port, workspace_dir=workspace_path, image_path=img_path)
            else:
                device = CirrosPC(name, console_port=port, workspace_dir=workspace_path)
        # === 新增 Cloud 占位判断 ===
        elif dtype == 'cloud':
            print(f"☁️ 注册云节点: {name} (物理桥接模式)")
            device = "CLOUD_NODE"  # 存个占位符，防止后面报错
        # 启动并存入字典
        # 启动并存入字典
        if device:
            if device != "CLOUD_NODE":  # <--- 拦截！如果是云节点，就别去执行 start()
                device.start()
            device_map[name] = device
        else:
            print(f"⚠️ 未知设备类型: {dtype}，跳过...")

    print("等待设备启动...")
    time.sleep(2)

    # 2. 建立连接
    print("\nStarting Link Setup...")
    for link in topo['links']:
        
        # === 情况 A: PC 连 路由 (3个参数) ===
        if len(link) == 3:
            pc_name = link[0]
            router_name = link[1]
            port_idx = link[2] # 1-based index

            print(f"Connecting PC: {pc_name} -> {router_name} Port {port_idx}")
            pc = device_map[pc_name]
            router = device_map[router_name]
            
            # PC 连接逻辑不变
            pc.connect_to_router(router, port_idx)

        # === 情况 B: 路由连路由 或 路由连云 (4个参数) ===
        elif len(link) == 4:
            # 判断是不是连向 CLOUD (支持 CLOUD 写在前面或后面)
            if link[2] == "CLOUD" or link[0] == "CLOUD":
                if link[2] == "CLOUD":
                    router_name, router_port, cloud_name, phys_nic = link
                else:
                    cloud_name, phys_nic, router_name, router_port = link

                print(f"Connecting Router: {router_name} Port {router_port} <--> {cloud_name} NIC {phys_nic}")
                
                router = device_map[router_name]
                # 获取路由器对应接口的底层 OVS 网桥
                bridge_A = router.ports[router_port - 1]['bridge']
                
                # 1. 激活物理网卡
                subprocess.run(f"sudo ip link set {phys_nic} up", shell=True)
                # 2. 将物理网卡强制挂载到路由器的 OVS 网桥上
                subprocess.run(f"sudo ovs-vsctl add-port {bridge_A.name} {phys_nic}", shell=True)
                print(f"✅ 成功将物理网卡 {phys_nic} 桥接到 {bridge_A.name}")

            else:
                # 这是你原本的 路由 连 路由 的逻辑
                rA_name = link[0]
                rA_port = link[1]
                rB_name = link[2]
                rB_port = link[3]

                print(f"Connecting Router: {rA_name} Port {rA_port} <--> {rB_name} Port {rB_port}")
                
                rA = device_map[rA_name]
                rB = device_map[rB_name]

                bridge_A = rA.ports[rA_port - 1]['bridge']
                bridge_B = rB.ports[rB_port - 1]['bridge']

                create_veth_link(bridge_A.name, bridge_B.name)

        else:
            print(f"⚠️ 无法识别的连接格式: {link}")

    return device_map

if __name__ == "__main__":
    # 1. 检查用户有没有输入足够的参数
    if len(sys.argv) < 2:
        print("用法错误！请使用以下格式:")
        print("  python main.py load <yaml_file>")
        print("  python main.py clean")
        exit(1)

    # 2. 获取“动作” (load / clean)
    action = sys.argv[1]

    if action == "load":
        # 确保用户指定了文件名
        if len(sys.argv) < 3:
            print("❌ 错误: 请指定要加载的 topology 文件路径。")
            exit(1)
            
        yaml_file = sys.argv[2]

        cleanup_environment() # 启动前先清场
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
        # --- 清理逻辑 ---
        cleanup_environment()
        print("✅ 手动清理完成。")

    else:
        print(f"❌ 未知命令: {action}")

