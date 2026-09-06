terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.101"
    }
  }
}
provider "proxmox" {
  endpoint = var.proxmox_endpoint
  insecure = true
  username = var.proxmox_username
  password = var.proxmox_password
  ssh {
    agent    = false
    username = "root"
    password = var.proxmox_ssh_password
    node {
      name    = "srv-heavy-1"
      address = "192.168.20.67"
    }
  }
}
resource "proxmox_virtual_environment_vm" "voice" {
  vm_id       = 209
  name        = "voice-changer"
  description = "Private VPN voice-effect demo; exclusive GTX 1050 Ti"
  tags        = ["voice", "gpu", "private", "ubuntu"]
  node_name   = "srv-heavy-1"
  on_boot     = true
  started     = true
  machine     = "q35"
  bios        = "ovmf"
  cpu {
    cores = 4
    type  = "host"
  }
  memory {
    dedicated = 4096
    floating  = 0
  }
  disk {
    datastore_id = "local-lvm"
    interface    = "scsi0"
    file_id      = "local:iso/ubuntu-24.04-server-cloudimg-amd64.img"
    size         = 64
    file_format  = "raw"
    iothread     = true
    discard      = "on"
    ssd          = true
  }
  network_device {
    bridge = "vmbr0"
    model  = "virtio"
  }
  initialization {
    datastore_id = "local-lvm"
    dns { servers = ["192.168.20.12", "10.19.87.1"] }
    ip_config {
      ipv4 {
        address = "192.168.20.70/24"
        gateway = "192.168.20.1"
      }
    }
    user_account {
      username = "ubuntu"
      keys     = [trimspace(file("/home/oleg/.ssh/id_ed25519.pub"))]
    }
  }
  efi_disk {
    datastore_id = "local-lvm"
    type         = "4m"
  }
  serial_device {}
  vga { type = "std" }
  hostpci {
    device = "hostpci0"
    id     = "0000:03:00.0"
    pcie   = true
  }
  boot_order = ["scsi0"]
}
