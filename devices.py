import subprocess
import os
from network import TapInterface, Bridge
import random
import hashlib

class QEMUDevice:
    """所有虚拟机的基类"""
    def __init__(self, name, memory, image_path, console_port):
        self.name = name
        self.memory = memory
        self.image_path = image_path
        self.console_port = console_port
        self.process = None
        self.interfaces = {} # 存储 {端口名: TapInterface对象}

    def stop(self):
        if self.process:
            print(f"正在终止 {self.name} (PID: {self.process.pid})...")
            self.process.terminate() # 发送 SIGTERM 信号
            try:
                self.process.wait(timeout=2) # 等它 2 秒自己死
            except subprocess.TimeoutExpired:
                self.process.kill() # 不死就强制杀
            self.process = None

class NE40Router(QEMUDevice):
    def __init__(self, name, console_port, workspace_dir, image_path="/home/liwd/imgs/router/NE40_Base.qcow2"):
        super().__init__(name, 2048, image_path, console_port)
        
        self.workspace_dir = workspace_dir
        filename = f"{self.name}.qcow2"
        self.overlay_image_path = os.path.join(workspace_dir, filename)
        
        self.mgmt_tap = TapInterface(f"tap_{name}_mgmt")
        self.internal_tap = TapInterface(f"tap_{name}_int")
        # 预定义 4 个业务口
        self.ports = [] 
        for i in range(1, 5):
            # 创建 tap_R1_ge1, tap_R1_ge2...
            tap = TapInterface(f"tap_{name}_ge{i}")
            # 创建专属网桥 br_R1_ge1 (SDN 模式)
            bridge = Bridge(f"br_{name}_ge{i}")
            self.ports.append({'tap': tap, 'bridge': bridge})

    def create_overlay(self):
        # 1. 检查工作区目录
        if not os.path.exists(self.workspace_dir):
            os.makedirs(self.workspace_dir, exist_ok=True)

        # 2. 【关键判断】如果文件已经存在，绝对不要覆盖！
        if os.path.exists(self.overlay_image_path):
            print(f"💾 [复用] 检测到 {self.name} 的历史数据 ({self.overlay_image_path})，将保留配置启动。")
            return

        # 3. 如果文件不存在，才创建新的
        print(f"💿 [新建] 正为 {self.name} 初始化新的磁盘文件...")
        abs_base = os.path.abspath(self.image_path)
        abs_overlay = os.path.abspath(self.overlay_image_path)
        
        cmd = f"qemu-img create -f qcow2 -b '{abs_base}' -F qcow2 '{abs_overlay}'"
        subprocess.run(cmd, shell=True, check=True)

    def generate_deterministic_mac(self, port_identifier):
        """
        根据 (设备名 + 端口标识) 生成固定的 MAC 地址
        例如: R1 的第1个口，永远生成同一个 MAC
        """
        # 1. 构造唯一字符串，例如 "R1_mgmt" 或 "R1_ge1"
        unique_str = f"{self.name}_{port_identifier}"
        
        # 2. 计算 MD5 哈希
        hash_object = hashlib.md5(unique_str.encode())
        hex_dig = hash_object.hexdigest() # 得到类似 "d41d8cd98f..."
        
        # 3. 截取哈希值的前8位，拼凑成 MAC 后缀
        # 格式: 50:00:xx:xx:xx:xx
        return "50:00:%s:%s:%s:%s" % (
            hex_dig[0:2], hex_dig[2:4], hex_dig[4:6], hex_dig[6:8]
        )

    def start(self):
        # 1. 准备工作：创建增量盘
        self.create_overlay()
        
        print(f"🚀 正在启动 {self.name} | Console端口: {self.console_port} ...")

        # 2. 初始化命令列表 (Base Command)
        # 建议使用列表，避免 shell=True 带来的转义问题和安全隐患
        cmd = [
            "sudo", "qemu-system-x86_64",
            "-name", self.name,
            "-m", str(self.memory),
            "-cpu", "host",
            "-smp", "2",
            "-enable-kvm",
            "-machine", "type=pc,accel=kvm",
            "-drive", f"file={self.overlay_image_path},format=qcow2,if=virtio",
            "-nographic",
            # 【关键】关闭 QEMU monitor 输出，防止干扰终端
            "-monitor", "none"
        ]

        # 3. 配置固定接口 (Mgmt & Internal)
        
        # --- 第1个口: Mgmt ---
        self.mgmt_tap.destroy() # 先清理
        self.mgmt_tap.create()
        mac_m = self.generate_deterministic_mac("mgmt")
        
        # 将参数 append 到列表中
        cmd.extend([
            "-device", f"virtio-net-pci,netdev=n0,mac={mac_m},bus=pci.0,addr=0x10",
            "-netdev", f"tap,id=n0,ifname={self.mgmt_tap.name},script=no,downscript=no"
        ])

        # --- 第2个口: Internal ---
        self.internal_tap.destroy() # 先清理
        self.internal_tap.create()
        mac_int = self.generate_deterministic_mac("internal")
        
        cmd.extend([
            "-device", f"virtio-net-pci,netdev=n1,mac={mac_int},bus=pci.0,addr=0x11",
            "-netdev", f"tap,id=n1,ifname={self.internal_tap.name},script=no,downscript=no"
        ])

        # 4. 配置动态业务口 (GE Interfaces)
        pci_base = 0x12
        for idx, port in enumerate(self.ports):
            tap = port['tap']
            bridge = port['bridge']
            
            # 底层网络设施构建
            tap.destroy() # 先清理旧的
            tap.create()
            bridge.create()
            tap.plug_into(bridge)
            
            # 生成确定性 MAC
            mac_port = self.generate_deterministic_mac(f"ge{idx+1}")
            
            # 追加参数
            cmd.extend([
                "-device", f"virtio-net-pci,netdev=n{idx+2},mac={mac_port},bus=pci.0,addr={hex(pci_base+idx)}",
                "-netdev", f"tap,id=n{idx+2},ifname={tap.name},script=no,downscript=no"
            ])

        # 5. 添加串口参数
        cmd.extend([
            "-serial", f"telnet:127.0.0.1:{self.console_port},server,nowait"
        ])

        # 6. 【核心】使用 Popen 启动并接管 IO
        try:
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,  
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.PIPE     
            )

            # 7. 启动状态检查 (0.5秒)
            try:
                self.process.wait(timeout=0.5)
                # 如果没超时，说明进程挂了
                _, stderr_data = self.process.communicate()
                print(f"❌ {self.name} 启动失败！")
                print(f"🔍 错误日志: {stderr_data.decode().strip()}")
            except subprocess.TimeoutExpired:
                # 超时说明进程还在跑，成功
                print(f"✅ {self.name} 已在后台运行 (PID: {self.process.pid})")
                if self.process.stderr:
                    self.process.stderr.close()

        except Exception as e:
            print(f"💥 启动异常: {e}")

class H3CSwitch(QEMUDevice):
    def __init__(self, name, console_port, workspace_dir, image_path="/home/liwd/imgs/siwtcher/H3C_Base.qcow2"):
        super().__init__(name, 1024, image_path, console_port)
        
        self.workspace_dir = workspace_dir
        filename = f"{self.name}.qcow2"
        self.overlay_image_path = os.path.join(workspace_dir, filename)
        
        self.mgmt_tap = TapInterface(f"tap_{name}_mgmt")
        self.internal_tap = TapInterface(f"tap_{name}_int")
        # 预定义 4 个业务口
        self.ports = [] 
        for i in range(1, 5):
            # 创建 tap_S1_ge1, tap_S1_ge2...
            tap = TapInterface(f"tap_{name}_ge{i}")
            # 创建专属网桥 br_S1_ge1 (SDN 模式)
            bridge = Bridge(f"br_{name}_ge{i}")
            self.ports.append({'tap': tap, 'bridge': bridge})

    def create_overlay(self):
        # 1. 检查工作区目录
        if not os.path.exists(self.workspace_dir):
            os.makedirs(self.workspace_dir, exist_ok=True)

        # 2. 【关键判断】如果文件已经存在，绝对不要覆盖！
        if os.path.exists(self.overlay_image_path):
            print(f"💾 [复用] 检测到 {self.name} 的历史数据 ({self.overlay_image_path})，将保留配置启动。")
            return

        # 3. 如果文件不存在，才创建新的
        print(f"💿 [新建] 正为 {self.name} 初始化新的磁盘文件...")
        abs_base = os.path.abspath(self.image_path)
        abs_overlay = os.path.abspath(self.overlay_image_path)
        
        cmd = f"qemu-img create -f qcow2 -b '{abs_base}' -F qcow2 '{abs_overlay}'"
        subprocess.run(cmd, shell=True, check=True)

    def generate_deterministic_mac(self, port_identifier):
        """
        根据 (设备名 + 端口标识) 生成固定的 MAC 地址
        例如: R1 的第1个口，永远生成同一个 MAC
        """
        # 1. 构造唯一字符串，例如 "S1_mgmt" 或 "S1_ge1"
        unique_str = f"{self.name}_{port_identifier}"
        
        # 2. 计算 MD5 哈希
        hash_object = hashlib.md5(unique_str.encode())
        hex_dig = hash_object.hexdigest() # 得到类似 "d41d8cd98f..."
        
        # 3. 截取哈希值的前8位，拼凑成 MAC 后缀
        # 格式: 50:00:xx:xx:xx:xx
        return "50:00:%s:%s:%s:%s" % (
            hex_dig[0:2], hex_dig[2:4], hex_dig[4:6], hex_dig[6:8]
        )

    def start(self):
        # 1. 准备工作：创建增量盘
        self.create_overlay()
        
        print(f"🚀 正在启动 {self.name} | Console端口: {self.console_port} ...")

        # 2. 初始化命令列表 (Base Command)
        # 建议使用列表，避免 shell=True 带来的转义问题和安全隐患
        cmd = [
            "sudo", "qemu-system-x86_64",
            "-name", self.name,
            "-m", str(self.memory),
            "-cpu", "host",
            "-smp", "2",
            "-enable-kvm",
            "-machine", "type=pc,accel=kvm",
            "-drive", f"file={self.overlay_image_path},format=qcow2,if=virtio",
            "-nographic",
            # 【关键】关闭 QEMU monitor 输出，防止干扰终端
            "-monitor", "none"
        ]

        # 3. 配置固定接口 (Mgmt & Internal)
        
        # --- 第1个口: Mgmt ---
        self.mgmt_tap.destroy() # 先清理
        self.mgmt_tap.create()
        mac_m = self.generate_deterministic_mac("mgmt")
        
        # 将参数 append 到列表中
        cmd.extend([
            "-device", f"e1000,netdev=n0,mac={mac_m},bus=pci.0,addr=0x03",
            "-netdev", f"tap,id=n0,ifname={self.mgmt_tap.name},script=no,downscript=no"
        ])

        # # --- 第2个口: Internal ---
        # self.internal_tap.destroy() # 先清理
        # self.internal_tap.create()
        # mac_int = self.generate_deterministic_mac("internal")
        
        # cmd.extend([
        #     "-device", f"e1000,netdev=n1,mac={mac_int},bus=pci.0,addr=0x04",
        #     "-netdev", f"tap,id=n1,ifname={self.internal_tap.name},script=no,downscript=no"
        # ])

        # 4. 配置动态业务口 (GE Interfaces)
        pci_base = 0x04
        for idx, port in enumerate(self.ports):
            tap = port['tap']
            bridge = port['bridge']
            
            # 底层网络设施构建
            tap.destroy() # 先清理旧的
            tap.create()
            bridge.create()
            tap.plug_into(bridge)
            
            # 生成确定性 MAC
            mac_port = self.generate_deterministic_mac(f"ge{idx+1}")
            
            # 追加参数
            cmd.extend([
                "-device", f"e1000,netdev=n{idx+2},mac={mac_port},bus=pci.0,addr={hex(pci_base+idx)}",
                "-netdev", f"tap,id=n{idx+2},ifname={tap.name},script=no,downscript=no"
            ])

        # 5. 添加串口参数
        cmd.extend([
            "-serial", f"telnet:127.0.0.1:{self.console_port},server,nowait"
        ])

        # 6. 【核心】使用 Popen 启动并接管 IO
        try:
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,  
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.PIPE     
            )

            # 7. 启动状态检查 (0.5秒)
            try:
                self.process.wait(timeout=0.5)
                # 如果没超时，说明进程挂了
                _, stderr_data = self.process.communicate()
                print(f"❌ {self.name} 启动失败！")
                print(f"🔍 错误日志: {stderr_data.decode().strip()}")
            except subprocess.TimeoutExpired:
                # 超时说明进程还在跑，成功
                print(f"✅ {self.name} 已在后台运行 (PID: {self.process.pid})")
                if self.process.stderr:
                    self.process.stderr.close()

        except Exception as e:
            print(f"💥 启动异常: {e}")


class CirrosPC(QEMUDevice):
    def __init__(self, name, console_port, workspace_dir, image_path="/home/liwd/imgs/pc/alpine_Base.qcow2"):
        super().__init__(name, 256, image_path, console_port)

        self.workspace_dir = workspace_dir
        filename = f"{self.name}.qcow2"
        self.overlay_image_path = os.path.join(workspace_dir, filename)

        self.eth0 = TapInterface(f"tap_{name}")

    def generate_deterministic_mac(self, port_identifier):
        """
        根据 (设备名 + 端口标识) 生成固定的 MAC 地址
        例如: R1 的第1个口，永远生成同一个 MAC
        """
        # 1. 构造唯一字符串，例如 "S1_mgmt" 或 "S1_ge1"
        unique_str = f"{self.name}_{port_identifier}"
        
        # 2. 计算 MD5 哈希
        hash_object = hashlib.md5(unique_str.encode())
        hex_dig = hash_object.hexdigest() # 得到类似 "d41d8cd98f..."
        
        # 3. 截取哈希值的前8位，拼凑成 MAC 后缀
        # 格式: 50:00:xx:xx:xx:xx
        return "50:00:%s:%s:%s:%s" % (
            hex_dig[0:2], hex_dig[2:4], hex_dig[4:6], hex_dig[6:8]
        )

    def create_overlay(self):
        # 1. 检查工作区目录
        if not os.path.exists(self.workspace_dir):
            os.makedirs(self.workspace_dir, exist_ok=True)

        # 2. 【关键判断】如果文件已经存在，绝对不要覆盖！
        if os.path.exists(self.overlay_image_path):
            print(f"💾 [复用] 检测到 {self.name} 的历史数据 ({self.overlay_image_path})，将保留配置启动。")
            return

        # 3. 如果文件不存在，才创建新的
        print(f"💿 [新建] 正为 {self.name} 初始化新的磁盘文件...")
        abs_base = os.path.abspath(self.image_path)
        abs_overlay = os.path.abspath(self.overlay_image_path)
        
        cmd = f"qemu-img create -f qcow2 -b '{abs_base}' -F qcow2 '{abs_overlay}'"
        subprocess.run(cmd, shell=True, check=True)

    def start(self):
        self.create_overlay()
        self.eth0.create()
        print(f"💻 启动 {self.name} | Console端口: {self.console_port} ...")
        
        # serial_cmd = f"-serial telnet:127.0.0.1:{self.console_port},server,nowait"
        
        # cmd = (f"sudo qemu-system-x86_64 -name {self.name} -m {self.memory} "
        #        f"-drive file={self.overlay_image_path},if=virtio -nographic "
        #        f"-netdev tap,id=n0,ifname={self.eth0.name},script=no,downscript=no "
        #        f"-device virtio-net-pci,netdev=n0 {serial_cmd} &")
        mac_port = self.generate_deterministic_mac(self.eth0.name)

        cmd = [
            "sudo", "qemu-system-x86_64",
            "-name", self.name,
            "-m", str(self.memory),
            "-drive", f"file={self.overlay_image_path},format=qcow2,if=virtio",
            "-nographic",
            
            # 网络参数
            "-netdev", f"tap,id=n0,ifname={self.eth0.name},script=no,downscript=no",
            "-device", f"e1000,netdev=n0,mac={mac_port}",
            
            # 串口参数 
            "-serial", f"telnet:127.0.0.1:{self.console_port},server,nowait",
            
            # 让 QEMU 自己的控制台不要占用 stdio，而是去 null
            "-monitor", "none" 
        ]
        
        try:
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,  # 切断键盘输入，让 QEMU 别抢信号
                stdout=subprocess.DEVNULL, # 把 QEMU 的废话扔进黑洞
                stderr=subprocess.PIPE     # 用于检测启动失败
            )
            
            # 健康检查：给它 0.5 秒，看它会不会暴毙
            try:
                # wait 的作用是看进程是否结束。如果 0.5s 内结束了，说明启动失败
                self.process.wait(timeout=0.5)
                
                # 如果代码走到这里，说明进程已经挂了，读取报错信息
                _, stderr_data = self.process.communicate()
                print(f"❌ {self.name} 启动失败！")
                print(f"🔍 错误日志: {stderr_data.decode().strip()}")
                
            except subprocess.TimeoutExpired:
                # 如果超时（timeout），说明 0.5s 后进程还在跑 -> 启动成功！
                print(f"✅ {self.name} 已在后台运行 (PID: {self.process.pid})")
                # 即使成功了，也要关闭 stderr 管道，防止缓冲区填满卡死
                if self.process.stderr:
                    self.process.stderr.close()

        except Exception as e:
            print(f"💥 启动异常: {e}")

    def connect_to_router(self, router: NE40Router, port_index: int):
        """用户接口：连接到路由器的第几个口"""
        # port_index 从 1 开始
        if port_index < 1 or port_index > len(router.ports):
            print("❌ 端口号越界")
            return
            
        target_bridge = router.ports[port_index-1]['bridge']
        print(f"⚙️ 正在将 {self.name} 连接到 {router.name} 的 Port {port_index}...")
        self.eth0.plug_into(target_bridge)

class CloudDevice:
    """
    [预留类] 代表外部设备 (物理机、其他虚拟化软件的网卡)
    """
    def __init__(self, name, target_interface):
        self.name = name
        self.target_interface = target_interface # 比如 "eth0" 或 "vmnet8"

    def connect_to_bridge(self, bridge):
        # 调用 bridge 的绑定物理接口方法
        bridge.bind_physical_interface(self.target_interface)

    def start(self):
        # 外部设备不需要启动命令，它是已经存在的
        print(f"☁️ 桥接云节点 {self.name} 已就绪，映射接口: {self.target_interface}")