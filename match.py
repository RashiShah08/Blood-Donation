# blood_match.py

# ---------------------------
# Blood compatibility dataset
# ---------------------------
compatibility = {
    "A+": {"donate_to": ["A+", "AB+"], "receive_from": ["A+", "A-", "O+", "O-"]},
    "A-": {"donate_to": ["A+", "A-", "AB+", "AB-"], "receive_from": ["A-", "O-"]},
    "B+": {"donate_to": ["B+", "AB+"], "receive_from": ["B+", "B-", "O+", "O-"]},
    "B-": {"donate_to": ["B+", "B-", "AB+", "AB-"], "receive_from": ["B-", "O-"]},
    "AB+": {"donate_to": ["AB+"], "receive_from": ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]},  # universal recipient
    "AB-": {"donate_to": ["AB+", "AB-"], "receive_from": ["A-", "B-", "AB-", "O-"]},
    "O+": {"donate_to": ["A+", "B+", "AB+", "O+"], "receive_from": ["O+", "O-"]},
    "O-": {"donate_to": ["A+", "B+", "AB+", "O+", "A-", "B-", "AB-", "O-"], "receive_from": ["O-"]},  # universal donor
}

# ---------------------------
# Main Program
# ---------------------------
def main():
    role = input("Are you a donor or a patient? (donor/patient): ").strip().lower()
    
    if role == "donor":
        donor_blood = input("Enter your blood type (e.g., A+, O-, B+): ").strip().upper()
        if donor_blood in compatibility:
            print(f"\nAs a donor with {donor_blood}, you can donate to: {', '.join(compatibility[donor_blood]['donate_to'])}")
        else:
            print("Invalid blood type entered.")

    elif role == "patient":
        patient_blood = input("Enter the patient's blood type (e.g., A+, O-, B+): ").strip().upper()
        urgency = input("Is it urgent? (yes/no): ").strip().lower()

        if patient_blood in compatibility:
            donors = compatibility[patient_blood]['receive_from']
            print(f"\nA patient with {patient_blood} can receive blood from: {', '.join(donors)}")

            if urgency == "yes":
                print("⚠️ Urgent case: Prioritize O- donors first if available!")
        else:
            print("Invalid blood type entered.")
    else:
        print("Please type either 'donor' or 'patient'.")

if __name__ == "__main__":
    main()
