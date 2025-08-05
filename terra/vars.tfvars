# terraform.tfvars

aws_region                  = "us-east-1"
my_ip                       = "YOUR_PUBLIC_IP/32"  # <-- CHANGE THIS! Example: "99.110.22.55/32"
public_key_path             = "~/.ssh/id_rsa.pub"    # <-- Update if your key is elsewhere
# vpc_id                    = "vpc-0123456789abcdef0" # <-- Uncomment and set if NOT using the default VPC
# sc_product_ami_output_key = "MyAmiIdOutputName" # <-- Uncomment and set to the real output key from your SC Product