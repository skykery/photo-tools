import os
import subprocess
import argparse

def find_raw_folders(base_path, dry_run=False, first_only=False, batch_size=None):
    executable = "/Applications/Adobe DNG Converter.app/Contents/MacOS/Adobe DNG Converter"
    candidate_data = []

    if not os.path.exists(base_path):
        print(f"Eroare: Ruta {base_path} nu este accesibilă.")
        return

    print(f"--- Scanare și sortare rute în: {base_path} ---")
    
    for root, dirs, files in os.walk(base_path):
        if not files:
            continue
            
        orfs = [f for f in files if f.lower().endswith('.orf')]
        has_dng = any(f.lower().endswith('.dng') for f in files)

        if orfs and not has_dng:
            # Obținem data creării (sau modificării dacă ctime nu e disponibil corect pe SMB)
            try:
                creation_time = os.path.getctime(root)
            except OSError:
                creation_time = os.path.getmtime(root)
            
            candidate_data.append({
                'path': root,
                'created_at': creation_time,
                'count': len(orfs)
            })

    if not candidate_data:
        print("\nNu s-au găsit foldere noi pentru procesare.")
        return

    # SORTARE: Sortăm după created_at (de la cele mai vechi la cele mai noi)
    # Dacă vrei cele mai noi primele, pune reverse=True
    candidate_data.sort(key=lambda x: x['created_at'])

    total_gasite = len(candidate_data)
    print(f"\nTotal foldere candidate găsite: {total_gasite}")
    
    # Aplicăm limitările
    if first_only:
        candidate_data = candidate_data[:1]
    elif batch_size:
        candidate_data = candidate_data[:batch_size]

    if dry_run:
        print("\n--- MOD DRY-RUN (Sortat după dată) ---")
        for item in candidate_data:
            from datetime import datetime
            dt = datetime.fromtimestamp(item['created_at']).strftime('%Y-%m-%d %H:%M:%S')
            print(f"[{dt}] {item['path']} ({item['count']} ORF-uri)")
    else:
        confirm = input(f"\nVrei să începi conversia pentru {len(candidate_data)} folder(e)? (y/n): ")
        if confirm.lower() == 'y':
            for i, item in enumerate(candidate_data, 1):
                folder = item['path']
                print(f"\n[PROGRES {i}/{len(candidate_data)}] Se procesează: {folder}")
                
                # Setările din screenshot-ul tău: dng1.7, lossy, fast load
                cmd = f'"{executable}" -c -lossy -p1 -dng1.7 -fl "{folder}"/*.ORF'
                
                try:
                    subprocess.run(cmd, shell=True, check=True)
                    print(f"[SUCCESS] Finalizat: {folder}")
                except subprocess.CalledProcessError as e:
                    print(f"[ERROR] Eșec la folderul {folder}. Cod: {e.returncode}")
            print("\n--- Proces batch finalizat! ---")
        else:
            print("Operațiune anulată.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DNG Converter cu Sortare după Dată")
    parser.add_argument("--path", default="/Volumes/Poze/DSLR", help="Ruta de bază")
    parser.add_argument("--dry-run", action="store_true", help="Afișează rutele sortate")
    parser.add_argument("--first-only", "-f", action="store_true", help="Cea mai veche rută")
    parser.add_argument("--batch-size", "-b", type=int, help="Număr de rute")
    
    args = parser.parse_args()
    find_raw_folders(args.path, args.dry_run, args.first_only, args.batch_size)
