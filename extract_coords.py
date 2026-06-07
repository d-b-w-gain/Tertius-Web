import sys
from PIL import Image
import numpy as np
from skimage.measure import find_contours, approximate_polygon

def extract_polygon(img_path):
    # Load image
    img = Image.open(img_path).convert('L')
    arr = np.array(img)
    
    # Binary image: lines are black (< 128)
    binary = arr < 128
    
    # Find contours
    # Since it's a line drawing, we want the outermost contour
    contours = find_contours(binary, 0.5)
    
    if not contours:
        print("No contours found.")
        return
        
    # Find the largest contour
    main_contour = max(contours, key=len)
    
    # Approximate polygon to reduce points to just the corners
    # Tolerance of 2.0 pixels
    poly = approximate_polygon(main_contour, tolerance=2.0)
    
    print(f"Extracted {len(poly)} vertices.")
    print("Coordinates (Y, X):")
    
    # Print the coordinates, formatted nicely
    for i, pt in enumerate(poly):
        print(f"Pt {i}: (X: {pt[1]:.1f}, Y: {pt[0]:.1f})")

if __name__ == "__main__":
    img_path = r"C:\Users\ben\.gemini\antigravity\brain\0fff33aa-cd8b-40bd-b488-1d633362d9ee\media__1780643388298.png"
    extract_polygon(img_path)
