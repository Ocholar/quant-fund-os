terraform {
  required_providers {
    oci = { source = "oracle/oci", version = ">= 6.0.0" }
  }
}

provider "oci" {
  region = var.region
}

# Minimal OCI scaffold. Fill tenancy/user/fingerprint/key vars in terraform.tfvars.
resource "oci_core_vcn" "quant_vcn" {
  compartment_id = var.compartment_ocid
  cidr_block     = "10.0.0.0/16"
  display_name   = "quant-vcn"
}

resource "oci_core_internet_gateway" "igw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.quant_vcn.id
  display_name   = "quant-igw"
}

resource "oci_core_route_table" "rt" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.quant_vcn.id
  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.igw.id
  }
}

resource "oci_core_security_list" "sl" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.quant_vcn.id
  display_name   = "quant-security"
  ingress_security_rules { protocol = "6" source = "0.0.0.0/0" tcp_options { min = 22 max = 22 } }
  ingress_security_rules { protocol = "6" source = "0.0.0.0/0" tcp_options { min = 80 max = 80 } }
  ingress_security_rules { protocol = "6" source = "0.0.0.0/0" tcp_options { min = 443 max = 443 } }
  ingress_security_rules { protocol = "6" source = "0.0.0.0/0" tcp_options { min = 8000 max = 8000 } }
  ingress_security_rules { protocol = "6" source = "0.0.0.0/0" tcp_options { min = 3000 max = 3000 } }
  egress_security_rules { protocol = "all" destination = "0.0.0.0/0" }
}

resource "oci_core_subnet" "subnet" {
  compartment_id      = var.compartment_ocid
  vcn_id              = oci_core_vcn.quant_vcn.id
  cidr_block          = "10.0.1.0/24"
  route_table_id      = oci_core_route_table.rt.id
  security_list_ids   = [oci_core_security_list.sl.id]
  display_name        = "quant-public-subnet"
}
