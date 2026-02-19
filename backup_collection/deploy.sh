#!/bin/bash
# ========================================
# Archive Platform Deployment Script
# ========================================

set -e

INVENTORY="inventory/hosts.ini"
PLAYBOOK="playbooks/site.yml"

echo "========================================="
echo "Archive Platform Deployment"
echo "========================================="
echo ""

# Check if inventory exists
if [ ! -f "$INVENTORY" ]; then
    echo "Error: Inventory file not found: $INVENTORY"
    exit 1
fi

# Check if playbook exists
if [ ! -f "$PLAYBOOK" ]; then
    echo "Error: Playbook not found: $PLAYBOOK"
    exit 1
fi

# Function to display menu
show_menu() {
    echo "Select deployment option:"
    echo "1) Deploy All Components"
    echo "2) Deploy Gateway Only"
    echo "3) Deploy Web Tier (ALB + Web)"
    echo "4) Deploy DB Tier (DBLB + DB)"
    echo "5) Deploy Frontend (Gateway + ALB + Web)"
    echo "6) Deploy Backend (DBLB + DB)"
    echo "7) Custom (Enter tags manually)"
    echo "8) Check Connectivity (Ping Test)"
    echo "9) Exit"
    echo ""
    read -p "Enter choice [1-9]: " choice
}

# Function to run playbook
run_playbook() {
    local tags=$1
    local cmd="ansible-playbook -i $INVENTORY $PLAYBOOK"
    
    if [ -n "$tags" ]; then
        cmd="$cmd --tags $tags"
    fi
    
    echo ""
    echo "Executing: $cmd"
    echo ""
    read -p "Continue? (y/n): " confirm
    
    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        $cmd
    else
        echo "Cancelled."
    fi
}

# Main loop
while true; do
    show_menu
    
    case $choice in
        1)
            echo "Deploying all components..."
            run_playbook ""
            ;;
        2)
            echo "Deploying Gateway only..."
            run_playbook "gateway"
            ;;
        3)
            echo "Deploying Web Tier..."
            run_playbook "web_tier"
            ;;
        4)
            echo "Deploying DB Tier..."
            run_playbook "db_tier"
            ;;
        5)
            echo "Deploying Frontend..."
            run_playbook "frontend"
            ;;
        6)
            echo "Deploying Backend..."
            run_playbook "backend"
            ;;
        7)
            read -p "Enter tags (comma-separated): " custom_tags
            run_playbook "$custom_tags"
            ;;
        8)
            echo "Testing connectivity..."
            ansible all -i $INVENTORY -m ping
            ;;
        9)
            echo "Exiting..."
            exit 0
            ;;
        *)
            echo "Invalid option. Please try again."
            ;;
    esac
    
    echo ""
    read -p "Press Enter to continue..."
    clear
done
