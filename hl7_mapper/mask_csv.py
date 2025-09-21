import pandas as pd
from faker import Faker
import random
import os
import argparse
from datetime import datetime

# Initialize Faker
fake = Faker()

# Function to get full path
def get_full_path(relative_path):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    return os.path.join(project_root, relative_path)

def detect_date_format(date_str):
    """Detect the format of a date string."""
    if pd.isna(date_str) or date_str == '':
        return None
    
    # Common date formats to try
    formats = [
        '%m/%d/%Y', '%Y-%m-%d', '%d/%m/%Y', 
        '%m-%d-%Y', '%Y/%m/%d', '%d-%m-%Y'
    ]
    
    for fmt in formats:
        try:
            datetime.strptime(date_str, fmt)
            return fmt
        except ValueError:
            continue
    return None

def mask_data(df, columns_to_mask):
    # Convert columns with underscores to spaces for matching
    columns_to_mask = [col.replace('_', ' ') for col in columns_to_mask]
    
    for column in columns_to_mask:
        if column in df.columns:
            original_values = df[column].values
            if column in ['PATIENT FIRST', 'PATIENT MIDDLE', 'PATIENT LAST']:
                df[column] = [fake.first_name() if pd.notna(val) and val != '' else val for val in original_values] if column != 'PATIENT LAST' else [fake.last_name() if pd.notna(val) and val != '' else val for val in original_values]
            elif column == 'GENDER':
                df[column] = [fake.random_element(['M', 'F']) if pd.notna(val) and val != '' else val for val in original_values]
            elif column == 'DOB' or column in ['DISCHARGE DATE', 'ADMIN DATE']:
                # Process each date value individually to preserve its format
                new_dates = []
                for val in original_values:
                    if pd.notna(val) and val != '':
                        date_format = detect_date_format(val)
                        if date_format:
                            if column == 'DOB':
                                fake_date = fake.date_of_birth(minimum_age=18, maximum_age=90)
                            else:
                                fake_date = fake.date_between(start_date='-2y', end_date='today')
                            new_dates.append(fake_date.strftime(date_format))
                        else:
                            new_dates.append(val)  # Keep original if format not detected
                    else:
                        new_dates.append(val)
                df[column] = new_dates
            elif column == 'ADDRESS':
                df[column] = [fake.street_address().replace('\n', ' ') if pd.notna(val) and val != '' else val for val in original_values]
            elif column == 'CITY':
                df[column] = [fake.city() if pd.notna(val) and val != '' else val for val in original_values]
            elif column == 'STATE':
                df[column] = [fake.state_abbr() if pd.notna(val) and val != '' else val for val in original_values]
            elif column == 'ZIP':
                df[column] = [fake.zipcode() if pd.notna(val) and val != '' else val for val in original_values]
            elif column == 'PHONE':
                df[column] = [fake.phone_number() if pd.notna(val) and val != '' else val for val in original_values]
            elif column in ['DISCHARGE TIME', 'ADMIT TIME']:
                df[column] = [fake.time() if pd.notna(val) and val != '' else val for val in original_values]
            elif column == 'PCP':
                df[column] = [fake.name() if pd.notna(val) and val != '' else val for val in original_values]
            elif column == 'DIAGNOSIS':
                df[column] = [fake.sentence(nb_words=3) if pd.notna(val) and val != '' else val for val in original_values]
            elif column in ['SENDING ID', 'ACCOUNT NO', 'PATIENT ID']:
                def generate_masked_id(val):
                    if pd.notna(val) and val != '':
                        # Count leading zeros
                        leading_zeros = len(val) - len(val.lstrip('0'))
                        # Generate a random number with same number of digits as original (excluding leading zeros)
                        new_num = fake.random_number(digits=len(val.lstrip('0')))
                        # Format with leading zeros
                        return str(new_num).zfill(leading_zeros + len(str(new_num)))
                    return val
                df[column] = [generate_masked_id(str(val)) for val in original_values]
            else:
                df[column] = [fake.word() if pd.notna(val) and val != '' else val for val in original_values]
    return df

def mask_csv(input_file, output_file, columns_to_mask):
    # Read the CSV file
    df = pd.read_csv(input_file, keep_default_na=False)

    # Mask the data
    df_masked = mask_data(df, columns_to_mask)

    # Save the masked data to a new CSV file
    df_masked.to_csv(output_file, index=False, na_rep='')

    print(f"Masked data has been saved to {output_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mask sensitive data in CSV files.")
    parser.add_argument("--input_file", required=True, help="Path to the input CSV file")
    parser.add_argument("--output_file", required=True, help="Path to save the masked CSV file")
    parser.add_argument("--columns", nargs="+", default=['PATIENT FIRST', 'PATIENT MIDDLE', 'PATIENT LAST', 'GENDER', 'DOB', 'ADDRESS', 'CITY', 'STATE', 'ZIP', 'PHONE'], help="Columns to mask")
    
    args = parser.parse_args()
    
    input_path = get_full_path(args.input_file)
    output_path = get_full_path(args.output_file)
    
    mask_csv(input_path, output_path, args.columns)